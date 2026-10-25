"""Render numeric claims from the focused tables, then compile the 13-page note."""
from data import *
import re
import subprocess
import tempfile

def build():
    e=read('paired_effects');c=read('independent_effects');tokens={};claim_rows=[]
    def paired(s,a,r,m):
        return e[(e.scenario==s)&(e.configuration==a)&(e.reference==r)&(e.metric==m)].iloc[0]
    def independent(kind,s,a,m,rs=None):
        g=c[(c.kind==kind)&(c.scenario==s)&(c.configuration==a)&(c.metric==m)]
        if rs:g=g[g.reference_scenario==rs]
        assert len(g)==1,(kind,s,a,m,rs)
        return g.iloc[0]
    def put(key,row,percent=False,decimals=2,reduction=False,speed=False):
        fields=('speedup','speedup_low','speedup_high') if speed else ('ratio_of_means_pct','ratio_ci_low','ratio_ci_high') if percent else ('absolute_effect','ci_low','ci_high')
        v,lo,hi=[float(row[x]) for x in fields]
        if reduction:v,lo,hi=-v,-hi,-lo
        suffix=r'\%' if percent else r'$\times$' if speed else ''
        tokens[key]=f'{v:.{decimals}f}{suffix} [{lo:.{decimals}f}, {hi:.{decimals}f}]'
        claim_rows.append(dict(token=key,**{k:row[k] for k in ['scenario','reference_scenario','configuration','reference','metric','kind'] if k in row},field=fields[0],value=v,low=lo,high=hi,reduction=reduction))
    p=lambda s,a,r,m:paired(s,a,r,m)
    x=lambda kind,s,a,m,rs=None:independent(kind,s,a,m,rs)
    put('smt_speed',x('scaling','w16_all','rayon','elapsed_ms'),speed=True,decimals=3)
    put('smt_elapsed',x('scaling','w16_all','rayon','elapsed_ms'))
    for key,m in [('smt_cpu','task_clock_ms'),('smt_inst','instructions_per_wu'),('smt_cycles','cycles_per_wu')]:put(key,x('scaling','w16_all','rayon',m),percent=True,decimals=3 if key=='smt_inst' else 2)
    put('mpmc_speed',x('scaling','w16_all',M0,'elapsed_ms'),speed=True,decimals=3)
    put('mpmc_smt_cpu',x('scaling','w16_all',M0,'task_clock_ms'),percent=True)
    for key,s,a,r,m,perc,red,dec in [
        ('r2_cpu','w16_bursty_1500',R2,'rayon','task_clock_ms',True,True,2),
        ('r2_elapsed','w16_bursty_1500',R2,'rayon','elapsed_ms',False,False,3),
        ('r2_8_cpu','w8_bursty_1500',R2,'rayon','task_clock_ms',True,True,2),
        ('r2_8_elapsed','w8_bursty_1500',R2,'rayon','elapsed_ms',False,False,3),
        ('m_cpu','w16_fixed_1500',M0,'rayon','task_clock_ms',True,False,2),
        ('m_cycles','w16_fixed_1500',M0,'rayon','cycles_per_wu',True,False,2),
        ('m_inst','w16_fixed_1500',M0,'rayon','instructions_per_wu',True,False,2),
        ('m_branch','w16_fixed_1500',M0,'rayon','branch_misses_per_wu',True,False,2),
        ('m_paced_elapsed','w16_fixed_1500',M0,'rayon','elapsed_ms',False,False,3),
        ('bevy_cpu','w16_fixed_1500','bevy','rayon','task_clock_ms',True,False,2),
        ('spin_cpu','w16_fixed_1500',M100,M0,'task_clock_ms',True,False,2),
        ('spin_cpu_abs','w16_fixed_1500',M100,M0,'task_clock_ms',False,False,2),
        ('spin_elapsed','w16_fixed_1500',M100,M0,'elapsed_ms',False,False,3),
        ('spin8_cpu','w8_fixed_1500',M100,M0,'task_clock_ms',True,False,2),
        ('r_spin_fixed','w16_fixed_1500',R100,R0,'task_clock_ms',False,False,2),
        ('r_spin_burst','w16_bursty_1500',R100,R0,'task_clock_ms',False,False,2),
        ('cw_cpu','w16_fixed_1500',MCW,M0,'task_clock_ms',False,False,2),
        ('ab_cpu','w16_fixed_1500',MAB,M0,'task_clock_ms',False,False,2),
        ('m_all_elapsed','w16_all',M0,'rayon','elapsed_ms',False,False,3),
        ('m_all_cpu','w16_all',M0,'rayon','task_clock_ms',False,False,2),
        ('bevy_all_elapsed','w16_all','bevy','rayon','elapsed_ms',False,False,3),
        ('r_all_elapsed','w16_all',R0,'rayon','elapsed_ms',False,False,3),
        ('r_all_elapsed_pct','w16_all',R0,'rayon','elapsed_ms',True,False,2),
        ('r_all_cpu','w16_all',R0,'rayon','task_clock_ms',False,False,2),
        ('r_m_fixed_cpu','w16_fixed_1500',R0,M0,'task_clock_ms',True,True,2),
        ('r_m_fixed_cycles','w16_fixed_1500',R0,M0,'cycles_per_wu',True,False,2),
        ('r_m_fixed_inst','w16_fixed_1500',R0,M0,'instructions_per_wu',True,False,3),
        ('r_m_fixed_freq','w16_fixed_1500',R0,M0,'effective_ghz',True,False,2),
        ('r_m_fixed_elapsed','w16_fixed_1500',R0,M0,'elapsed_ms',False,False,3),
        ('r_m8_cpu','w8_fixed_1500',R0,M0,'task_clock_ms',True,True,2),
        ('r_m_burst_cpu','w16_bursty_1500',R0,M0,'task_clock_ms',True,True,2),
        ('r_m_burst_elapsed','w16_bursty_1500',R0,M0,'elapsed_ms',False,False,3)]:
        put(key,p(s,a,r,m),percent=perc,reduction=red,decimals=dec)
    for key,a,m,perc,red,dec in [
        ('burst_cpu','rayon','task_clock_ms',True,False,2),('burst_cpu_abs','rayon','task_clock_ms',False,False,2),
        ('burst_ctx','rayon','context_switches',True,True,2),('burst_cycles','rayon','cycles_per_wu',True,False,2),
        ('burst_inst','rayon','instructions_per_wu',True,False,3),('m_burst_cpu',M0,'task_clock_ms',True,False,2),
        ('m_burst_inst',M0,'instructions_per_wu',True,False,4)]:
        put(key,x('arrival','w16_bursty_1500',a,m),percent=perc,reduction=red,decimals=dec)
    put('r2_paced_scale',x('paced_workers','w16_bursty_1500',R2,'task_clock_ms'))
    put('ray_paced_scale',x('paced_workers','w16_bursty_1500','rayon','task_clock_ms'))
    put('history_cpu',x('change_vs_Rayon','w16_bursty_1500',R2,'task_clock_ms','w4_bursty_1500'))
    h=read('historical_interruption_sensitivity').query("metric == 'task_clock_ms'").iloc[0]
    tokens['history_omit']=f"{h.effect:.2f} [{h.ci_low:.2f}, {h.ci_high:.2f}]"
    source=Path(__file__).with_suffix('.tex').read_text()
    needed=set(re.findall(r'@@(\w+)@@',source));assert needed==set(tokens),(needed-set(tokens),set(tokens)-needed)
    for k,v in tokens.items():source=source.replace('@@'+k+'@@',v)
    (OUT/'report.tex').write_text(source)
    save(claim_rows,'report_claims')
    command=['xelatex']
    temporary=None
    if not subprocess.run(['kpsewhich','xelatex.fmt'],capture_output=True,text=True).stdout.strip():
        temporary=tempfile.TemporaryDirectory(prefix='bls-scaling-tex-')
        ini=subprocess.check_output(['kpsewhich','xelatex.ini'],text=True).strip()
        res=subprocess.run(['xetex','-ini','-etex','-jobname=xelatex','-progname=xelatex',ini],cwd=temporary.name,capture_output=True,text=True)
        assert res.returncode==0,res.stdout[-4000:]
        command+=['-fmt='+str(Path(temporary.name)/'xelatex.fmt')]
    for i in [1,2]:
        res=subprocess.run(command+['-interaction=nonstopmode','-halt-on-error','report.tex'],cwd=OUT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        (OUT/f'latex_pass_{i}.log').write_text(res.stdout)
        assert res.returncode==0,res.stdout[-5000:]
    if temporary:temporary.cleanup()
    print('PDF compiled:',OUT/'report.pdf',flush=True)

if __name__=='__main__':build()
