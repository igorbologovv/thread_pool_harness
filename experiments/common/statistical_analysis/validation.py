"""Validated physical alternatives and block-bootstrap polynomial curve uncertainty.

Extends regression.py; all polynomial fitting/selection uses its existing API.
"""
from collections import defaultdict
import numpy as np
from scipy.optimize import least_squares
from scipy.interpolate import BSpline
from scipy.linalg import null_space
from .regression import fit, predict, select
from .core import correlation


def physical_fit(x, y, family):
    x,y=np.asarray(x,float),np.asarray(y,float)
    if family=='natural_spline':
        # Three effective parameters: cubic B-spline with one training-median
        # knot, constrained to zero second derivatives at both boundaries.
        if len(x)<4 or len(np.unique(x))<4:raise ValueError('Spline requires residual degrees of freedom')
        center=float(min(x));scale=float(np.ptp(x));z=(x-center)/scale
        knots=[0.]*4+[float(np.median(z))]+[1.]*4
        basis=BSpline(knots,np.eye(5),3)
        natural=null_space(basis.derivative(2)([0.,1.]))
        a=basis(z)@natural
        coef,_,rank,_=np.linalg.lstsq(a,y,rcond=None)
        if rank!=3:raise ValueError('Rank-deficient natural spline')
        return dict(family=family,knots=knots,natural_basis=natural.tolist(),coefficients=coef.tolist(),center=center,scale=scale,
            effective_parameters=3,knot_rule='Median of training X levels only; natural boundary constraints; linear continuation outside training range for boundary validation only')
    if family=='saturation':
        # Finite-batch throughput can plateau; no fixed measured capacity is
        # borrowed from the held-out X acquisition.
        if len(x)<4 or np.min(x)<=0:raise ValueError('Insufficient saturation fit levels')
        scale=max(float(np.mean(y)),1e-12)
        solution=least_squares(lambda p:(np.minimum(p[0]*x,p[1])-y)/scale,
            [max(np.median(y/x),1e-9),max(y)],bounds=([0,1e-9],[np.inf,np.inf]),
            max_nfev=300)
        if not solution.success:raise ValueError('Saturation fit failed')
        return dict(family=family, slope=float(solution.x[0]),capacity=float(solution.x[1]),
            equation='min(slope*x, capacity)')
    if family=='capacity_hinge':
        # A priori knot at own-scheduler measured finite-batch saturation, X=100%.
        # Extra flexibility is allowed only if BOTH branches have >=2 levels.
        if sum(x<100)<2 or sum(x>100)<2:raise ValueError('Insufficient levels on both sides of physical capacity knot')
        z=(x-100)/100
        a=np.column_stack([np.ones(len(x)),z,np.maximum(z,0)])
        coef,_,rank,_=np.linalg.lstsq(a,y,rcond=None)
        if rank!=3 or len(x)<=3:raise ValueError('Unidentified hinge')
        return dict(family=family, coefficients=coef.tolist(),knot=100.,scale=100.,
            equation='b0+b1*((x-100)/100)+b2*max((x-100)/100,0)')
    raise ValueError(family)


def physical_predict(model,x):
    x=np.asarray(x,float)
    if model['family']=='natural_spline':return spline_matrix(model,x)@np.array(model['coefficients'])
    if model['family']=='saturation':return np.minimum(model['slope']*x,model['capacity'])
    z=(x-100)/100;b=model['coefficients']
    return b[0]+b[1]*z+b[2]*np.maximum(z,0)


def spline_matrix(model,x):
    z=(np.asarray(x,float)-model['center'])/model['scale']
    basis=BSpline(model['knots'],np.eye(5),3);natural=np.array(model['natural_basis'])
    clipped=np.clip(z,0,1)
    # Natural constraints apply at the endpoints; continue linearly for omitted
    # boundary prediction, instead of extending the last cubic polynomial.
    return (basis(clipped)+basis.derivative()(clipped)*(z-clipped)[...,None])@natural


def choose(x,y,max_degree,alternatives):
    degree,scores=select(x,y,max_degree)
    options=[dict(family='polynomial',mse=next(s['mse'] for s in scores if s['degree']==degree),degree=degree)]
    for family in alternatives:
        errors=[]
        try:
            for i in range(len(x)):
                keep=np.arange(len(x))!=i
                m=physical_fit(x[keep],y[keep],family)
                errors.append(float(physical_predict(m,x[i])-y[i])**2)
            options.append(dict(family=family,mse=float(np.mean(errors))))
        except ValueError:
            options.append(dict(family=family,mse=None,reason='Training folds cannot identify model; no validation claim'))
    valid=[r for r in options if r['mse'] is not None]
    return min(valid,key=lambda r:r['mse']),options


def compare(result, *, relationship, seed=20261105, bootstrap=2000):
    """Nested X-level CV of family/degree choice; conditional polynomial bands.

    X is fixed for offered/worker relations. CPU-vs-achieved uses noisy same-run
    X and receives no curve uncertainty claim or reliable continuous model.
    """
    groups=defaultdict(list)
    for c in result['conditions']:groups[(tuple(c['stratum'].items()),c['scheduler'])].append(c)
    rng=np.random.default_rng(seed)
    for model in result['regressions']:
        if model['status']!='ok':continue
        cs=sorted(groups[(tuple(model['stratum'].items()),model['scheduler'])],key=lambda c:c['x'])
        x=np.array([c['x'] for c in cs]);y=np.array([c['estimate'] for c in cs])
        model['empirical_estimates']=[dict(x=c['x'],estimate=c['estimate'],ci95=c['ci95']) for c in cs]
        families=['saturation'] if relationship in ('absolute_load','relative_load') else ['capacity_hinge','natural_spline'] if relationship=='cpu_load' else []
        best,options=choose(x,y,3,families)
        cv=[]
        for i in range(len(x)) if len(x)>=4 else []:
            keep=np.arange(len(x))!=i
            b,_=choose(x[keep],y[keep],3,families)
            if b['family']=='polynomial':p=float(predict(fit(x[keep],y[keep],b['degree']),x[i]))
            else:p=float(physical_predict(physical_fit(x[keep],y[keep],b['family']),x[i]))
            cv.append(dict(x=float(x[i]),observed=float(y[i]),predicted=p,residual=float(y[i]-p),family=b['family'],
                degree=b.get('degree'),extrapolation=bool(i==0 or i==len(x)-1)))
        rmse=float(np.sqrt(np.mean([r['residual']**2 for r in cv]))) if cv else None
        maxerr=max((abs(r['residual']) for r in cv),default=None)
        reference=100. if relationship=='relative_load' else float(np.mean(y))
        # Declared engineering accuracy criterion, not a calibrated hypothesis test.
        passes=bool(rmse is not None and rmse/reference<=.05 and maxerr/reference<=.10)
        if relationship=='cpu_achieved':passes=False
        model['predictive_comparison']=dict(selected=best,candidates=options,nested_cv=cv,nested_cv_rmse=rmse,
            interior_cv_rmse=float(np.sqrt(np.mean([r['residual']**2 for r in cv if not r['extrapolation']]))) if len(cv)>2 else None,
            max_absolute_error=maxerr,reference_scale=reference,passes_accuracy_screen=passes,
            criterion='Exploratory engineering tolerance: nested family-selection RMSE <=5% of mean response (100 percentage points for relative) and worst error <=10%. Not a formal model-validity test.',
            inference='All repeats and blocks of held-out experimental level omitted. Independent load acquisitions do not share draws; no block numbers are paired across acquisitions. End-level folds extrapolate only for validation. Display domains never extrapolate.',
            measurement_error='CPU-vs-achieved X is estimated from the same physical runs; both axes have correlated noise. X-level CV is descriptive, no errors-in-variables identification or reliable continuous response claimed.' if relationship=='cpu_achieved' else None)
        if best['family']!='polynomial':
            pm=physical_fit(x,y,best['family']);model['predictive_comparison']['alternative_model']=pm
            model['predictive_comparison']['curve']={'x':model['curve']['x'],'y':physical_predict(pm,model['curve']['x']).tolist()}
        residual=model['residuals']
        model['residual_diagnostics']=dict(residual_x_spearman=correlation([r['x'] for r in residual],[r['residual'] for r in residual]),
            abs_residual_fitted_spearman=correlation([r['fitted'] for r in residual],[abs(r['residual']) for r in residual]),
            block_residual_variance_by_x=[dict(x=c['x'],variance=float(np.var([r['residual'] for r in residual if r['x']==c['x']],ddof=1))) for c in cs],
            note='Descriptive heteroscedasticity/curvature diagnostics; block residuals are clustered. No IID residual tests.')
        if relationship in ('cpu_workers','cpu_achieved'):
            model['bootstrap_curve']=dict(status='not defensible',reason='Four discrete hardware levels / changing load and batch size' if relationship=='cpu_workers' else 'Noisy correlated same-run predictor; errors-in-variables model unavailable')
            continue
        # Conditional-on-degree, whole-block curve uncertainty. Shared block vectors
        # resample jointly. Baseline uncertainty is a shared scaling across levels.
        draws={}; columns=[]
        for c in cs:
            bs=sorted(c['block_means'],key=lambda b:str(b['block']))
            signature=tuple(b['block'] for b in bs)
            if signature not in draws:draws[signature]=rng.integers(0,len(bs),(bootstrap,len(bs)))
            columns.append(np.array([b['mean'] for b in bs])[draws[signature]].mean(axis=1))
        response=np.array(columns).T
        if relationship=='relative_load':
            baseline=cs[0].get('_baseline_blocks')
            if baseline is None:raise ValueError('Missing raw baseline blocks for curve uncertainty')
            b=np.array([r['mean'] for r in baseline]);b_draw=b[rng.integers(0,len(b),(bootstrap,len(b)))].mean(axis=1)
            response*=np.mean(b)/b_draw[:,None]
        fixed=model['model'];z=(x-fixed['center'])/fixed['scale']
        a=np.polynomial.polynomial.polyvander(z,fixed['degree'])
        coeff=response@np.linalg.pinv(a).T
        grid=np.array(model['curve']['x']);g=np.polynomial.polynomial.polyvander((grid-fixed['center'])/fixed['scale'],fixed['degree'])
        curves=coeff@g.T
        low,high=np.quantile(curves,[.025,.975],axis=0)
        model['bootstrap_curve']=dict(status='conditional pointwise polynomial mean-response interval',replicates=bootstrap,
            seed=seed,ci95_low=low.tolist(),ci95_high=high.tolist(),coefficient_ci95=np.quantile(coeff,[.025,.975],axis=0).tolist(),
            note='Degree/basis held fixed; uncertainty conditional on model selection, fixed plug-in load denominator and independent exchangeable blocks. No prediction interval, simultaneous band, or selection-bias correction. Relative Y includes independent shared capacity uncertainty; normalized X is held fixed.')
        if best['family']=='natural_spline':
            pm=model['predictive_comparison']['alternative_model']
            a=spline_matrix(pm,x);g=spline_matrix(pm,grid)
            spline_curves=(response@np.linalg.pinv(a).T)@g.T
            low,high=np.quantile(spline_curves,[.025,.975],axis=0)
            model['predictive_comparison']['alternative_bootstrap_curve']=dict(ci95_low=low.tolist(),ci95_high=high.tolist(),replicates=bootstrap,seed=seed,
                note='Conditional on selected natural-spline family, training-median knot and natural basis; independent whole-block mean-response uncertainty. No model-selection, prediction or simultaneous coverage claim.')

    return result
