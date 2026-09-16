
target/release/adaptive-threadpool-thesis:     file format elf64-x86-64


Disassembly of section .text:

000000000004c2c0 <<adaptive_threadpool_thesis::workload::compute_heavy::ComputeHeavyWorkload as adaptive_threadpool_thesis::workload::Workload>::execute>:
   4c2c0:	41 57                	push   %r15
   4c2c2:	41 56                	push   %r14
   4c2c4:	53                   	push   %rbx
   4c2c5:	48 81 ec 10 0a 00 00 	sub    $0xa10,%rsp
   4c2cc:	49 89 f7             	mov    %rsi,%r15
   4c2cf:	49 89 fe             	mov    %rdi,%r14
   4c2d2:	48 8d 9c 24 70 01 00 	lea    0x170(%rsp),%rbx
   4c2d9:	00 
   4c2da:	ba 00 02 00 00       	mov    $0x200,%edx
   4c2df:	48 89 df             	mov    %rbx,%rdi
   4c2e2:	31 f6                	xor    %esi,%esi
   4c2e4:	ff 15 d6 fb 0a 00    	call   *0xafbd6(%rip)        # fbec0 <memset@GLIBC_2.2.5>
   4c2ea:	49 8b 3f             	mov    (%r15),%rdi
   4c2ed:	49 8b 77 08          	mov    0x8(%r15),%rsi
   4c2f1:	49 8b 56 10          	mov    0x10(%r14),%rdx
   4c2f5:	48 39 fe             	cmp    %rdi,%rsi
   4c2f8:	0f 82 32 11 00 00    	jb     4d430 <<adaptive_threadpool_thesis::workload::compute_heavy::ComputeHeavyWorkload as adaptive_threadpool_thesis::workload::Workload>::execute+0x1170>
   4c2fe:	48 39 d6             	cmp    %rdx,%rsi
   4c301:	0f 87 29 11 00 00    	ja     4d430 <<adaptive_threadpool_thesis::workload::compute_heavy::ComputeHeavyWorkload as adaptive_threadpool_thesis::workload::Workload>::execute+0x1170>
   4c307:	48 39 f7             	cmp    %rsi,%rdi
   4c30a:	75 07                	jne    4c313 <<adaptive_threadpool_thesis::workload::compute_heavy::ComputeHeavyWorkload as adaptive_threadpool_thesis::workload::Workload>::execute+0x53>
   4c30c:	31 c0                	xor    %eax,%eax
   4c30e:	e9 00 11 00 00       	jmp    4d413 <<adaptive_threadpool_thesis::workload::compute_heavy::ComputeHeavyWorkload as adaptive_threadpool_thesis::workload::Workload>::execute+0x1153>
   4c313:	49 8b 46 08          	mov    0x8(%r14),%rax
   4c317:	48 c1 e6 0a          	shl    $0xa,%rsi
   4c31b:	48 01 c6             	add    %rax,%rsi
   4c31e:	48 c1 e7 0a          	shl    $0xa,%rdi
   4c322:	48 01 c7             	add    %rax,%rdi
   4c325:	0f 28 84 24 70 01 00 	movaps 0x170(%rsp),%xmm0
   4c32c:	00 
   4c32d:	0f 28 8c 24 80 01 00 	movaps 0x180(%rsp),%xmm1
   4c334:	00 
   4c335:	0f 29 8c 24 60 01 00 	movaps %xmm1,0x160(%rsp)
   4c33c:	00 
   4c33d:	0f 28 9c 24 90 01 00 	movaps 0x190(%rsp),%xmm3
   4c344:	00 
   4c345:	66 0f 28 a4 24 a0 01 	movapd 0x1a0(%rsp),%xmm4
   4c34c:	00 00 
   4c34e:	66 0f 28 ac 24 b0 01 	movapd 0x1b0(%rsp),%xmm5
   4c355:	00 00 
   4c357:	66 0f 28 b4 24 c0 01 	movapd 0x1c0(%rsp),%xmm6
   4c35e:	00 00 
   4c360:	66 0f 28 bc 24 d0 01 	movapd 0x1d0(%rsp),%xmm7
   4c367:	00 00 
   4c369:	66 44 0f 28 84 24 e0 	movapd 0x1e0(%rsp),%xmm8
   4c370:	01 00 00 
   4c373:	66 44 0f 28 8c 24 f0 	movapd 0x1f0(%rsp),%xmm9
   4c37a:	01 00 00 
   4c37d:	66 44 0f 28 94 24 00 	movapd 0x200(%rsp),%xmm10
   4c384:	02 00 00 
   4c387:	66 44 0f 28 9c 24 10 	movapd 0x210(%rsp),%xmm11
   4c38e:	02 00 00 
   4c391:	66 44 0f 28 a4 24 20 	movapd 0x220(%rsp),%xmm12
   4c398:	02 00 00 
   4c39b:	66 44 0f 28 ac 24 30 	movapd 0x230(%rsp),%xmm13
   4c3a2:	02 00 00 
   4c3a5:	66 44 0f 28 b4 24 40 	movapd 0x240(%rsp),%xmm14
   4c3ac:	02 00 00 
   4c3af:	66 44 0f 28 bc 24 50 	movapd 0x250(%rsp),%xmm15
   4c3b6:	02 00 00 
   4c3b9:	0f 28 8c 24 60 02 00 	movaps 0x260(%rsp),%xmm1
   4c3c0:	00 
   4c3c1:	0f 29 8c 24 e0 00 00 	movaps %xmm1,0xe0(%rsp)
   4c3c8:	00 
   4c3c9:	31 c0                	xor    %eax,%eax
   4c3cb:	0f 28 8c 24 70 02 00 	movaps 0x270(%rsp),%xmm1
   4c3d2:	00 
   4c3d3:	0f 29 8c 24 d0 00 00 	movaps %xmm1,0xd0(%rsp)
   4c3da:	00 
   4c3db:	0f 28 8c 24 80 02 00 	movaps 0x280(%rsp),%xmm1
   4c3e2:	00 
   4c3e3:	0f 29 8c 24 c0 00 00 	movaps %xmm1,0xc0(%rsp)
   4c3ea:	00 
   4c3eb:	0f 28 8c 24 90 02 00 	movaps 0x290(%rsp),%xmm1
   4c3f2:	00 
   4c3f3:	0f 29 8c 24 b0 00 00 	movaps %xmm1,0xb0(%rsp)
   4c3fa:	00 
   4c3fb:	0f 28 8c 24 a0 02 00 	movaps 0x2a0(%rsp),%xmm1
   4c402:	00 
   4c403:	0f 29 8c 24 a0 00 00 	movaps %xmm1,0xa0(%rsp)
   4c40a:	00 
   4c40b:	0f 28 8c 24 b0 02 00 	movaps 0x2b0(%rsp),%xmm1
   4c412:	00 
   4c413:	0f 29 8c 24 90 00 00 	movaps %xmm1,0x90(%rsp)
   4c41a:	00 
   4c41b:	0f 28 8c 24 c0 02 00 	movaps 0x2c0(%rsp),%xmm1
   4c422:	00 
   4c423:	0f 29 8c 24 80 00 00 	movaps %xmm1,0x80(%rsp)
   4c42a:	00 
   4c42b:	0f 28 8c 24 d0 02 00 	movaps 0x2d0(%rsp),%xmm1
   4c432:	00 
   4c433:	0f 29 4c 24 70       	movaps %xmm1,0x70(%rsp)
   4c438:	0f 28 8c 24 e0 02 00 	movaps 0x2e0(%rsp),%xmm1
   4c43f:	00 
   4c440:	0f 29 4c 24 60       	movaps %xmm1,0x60(%rsp)
   4c445:	0f 28 8c 24 f0 02 00 	movaps 0x2f0(%rsp),%xmm1
   4c44c:	00 
   4c44d:	0f 29 4c 24 50       	movaps %xmm1,0x50(%rsp)
   4c452:	0f 28 8c 24 00 03 00 	movaps 0x300(%rsp),%xmm1
   4c459:	00 
   4c45a:	0f 29 4c 24 40       	movaps %xmm1,0x40(%rsp)
   4c45f:	0f 28 8c 24 10 03 00 	movaps 0x310(%rsp),%xmm1
   4c466:	00 
   4c467:	0f 29 4c 24 30       	movaps %xmm1,0x30(%rsp)
   4c46c:	0f 28 8c 24 20 03 00 	movaps 0x320(%rsp),%xmm1
   4c473:	00 
   4c474:	0f 29 4c 24 20       	movaps %xmm1,0x20(%rsp)
   4c479:	0f 28 8c 24 30 03 00 	movaps 0x330(%rsp),%xmm1
   4c480:	00 
   4c481:	0f 29 4c 24 10       	movaps %xmm1,0x10(%rsp)
   4c486:	0f 28 8c 24 40 03 00 	movaps 0x340(%rsp),%xmm1
   4c48d:	00 
   4c48e:	0f 29 8c 24 50 01 00 	movaps %xmm1,0x150(%rsp)
   4c495:	00 
   4c496:	0f 28 8c 24 50 03 00 	movaps 0x350(%rsp),%xmm1
   4c49d:	00 
   4c49e:	0f 29 8c 24 40 01 00 	movaps %xmm1,0x140(%rsp)
   4c4a5:	00 
   4c4a6:	66 0f 28 8c 24 60 03 	movapd 0x360(%rsp),%xmm1
   4c4ad:	00 00 
   4c4af:	66 0f 29 8c 24 30 01 	movapd %xmm1,0x130(%rsp)
   4c4b6:	00 00 
   4c4b8:	0f 1f 84 00 00 00 00 	nopl   0x0(%rax,%rax,1)
   4c4bf:	00 
   4c4c0:	66 44 0f 29 bc 24 f0 	movapd %xmm15,0xf0(%rsp)
   4c4c7:	00 00 00 
   4c4ca:	66 44 0f 29 b4 24 00 	movapd %xmm14,0x100(%rsp)
   4c4d1:	01 00 00 
   4c4d4:	66 44 0f 29 ac 24 10 	movapd %xmm13,0x110(%rsp)
   4c4db:	01 00 00 
   4c4de:	66 44 0f 29 a4 24 70 	movapd %xmm12,0x370(%rsp)
   4c4e5:	03 00 00 
   4c4e8:	66 44 0f 29 9c 24 80 	movapd %xmm11,0x380(%rsp)
   4c4ef:	03 00 00 
   4c4f2:	66 44 0f 29 94 24 90 	movapd %xmm10,0x390(%rsp)
   4c4f9:	03 00 00 
   4c4fc:	66 44 0f 29 8c 24 a0 	movapd %xmm9,0x3a0(%rsp)
   4c503:	03 00 00 
   4c506:	66 44 0f 29 84 24 b0 	movapd %xmm8,0x3b0(%rsp)
   4c50d:	03 00 00 
   4c510:	66 0f 29 bc 24 c0 03 	movapd %xmm7,0x3c0(%rsp)
   4c517:	00 00 
   4c519:	66 0f 29 b4 24 d0 03 	movapd %xmm6,0x3d0(%rsp)
   4c520:	00 00 
   4c522:	66 0f 29 ac 24 e0 03 	movapd %xmm5,0x3e0(%rsp)
   4c529:	00 00 
   4c52b:	66 0f 29 a4 24 f0 03 	movapd %xmm4,0x3f0(%rsp)
   4c532:	00 00 
   4c534:	0f 29 1c 24          	movaps %xmm3,(%rsp)
   4c538:	0f 29 84 24 20 01 00 	movaps %xmm0,0x120(%rsp)
   4c53f:	00 
   4c540:	f2 0f 10 07          	movsd  (%rdi),%xmm0
   4c544:	f2 0f 10 4f 08       	movsd  0x8(%rdi),%xmm1
   4c549:	f2 0f 10 57 10       	movsd  0x10(%rdi),%xmm2
   4c54e:	f2 0f 10 5f 18       	movsd  0x18(%rdi),%xmm3
   4c553:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c556:	0f 29 84 24 f0 07 00 	movaps %xmm0,0x7f0(%rsp)
   4c55d:	00 
   4c55e:	0f 16 c9             	movlhps %xmm1,%xmm1
   4c561:	0f 29 8c 24 e0 07 00 	movaps %xmm1,0x7e0(%rsp)
   4c568:	00 
   4c569:	0f 16 d2             	movlhps %xmm2,%xmm2
   4c56c:	0f 29 94 24 d0 07 00 	movaps %xmm2,0x7d0(%rsp)
   4c573:	00 
   4c574:	0f 16 db             	movlhps %xmm3,%xmm3
   4c577:	0f 29 9c 24 c0 07 00 	movaps %xmm3,0x7c0(%rsp)
   4c57e:	00 
   4c57f:	f2 0f 10 47 20       	movsd  0x20(%rdi),%xmm0
   4c584:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c587:	0f 29 84 24 b0 07 00 	movaps %xmm0,0x7b0(%rsp)
   4c58e:	00 
   4c58f:	f2 0f 10 47 28       	movsd  0x28(%rdi),%xmm0
   4c594:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c597:	0f 29 84 24 a0 07 00 	movaps %xmm0,0x7a0(%rsp)
   4c59e:	00 
   4c59f:	f2 0f 10 47 30       	movsd  0x30(%rdi),%xmm0
   4c5a4:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c5a7:	0f 29 84 24 90 07 00 	movaps %xmm0,0x790(%rsp)
   4c5ae:	00 
   4c5af:	f2 0f 10 47 38       	movsd  0x38(%rdi),%xmm0
   4c5b4:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c5b7:	0f 29 84 24 80 07 00 	movaps %xmm0,0x780(%rsp)
   4c5be:	00 
   4c5bf:	f2 0f 10 47 40       	movsd  0x40(%rdi),%xmm0
   4c5c4:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c5c7:	0f 29 84 24 70 07 00 	movaps %xmm0,0x770(%rsp)
   4c5ce:	00 
   4c5cf:	f2 0f 10 47 48       	movsd  0x48(%rdi),%xmm0
   4c5d4:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c5d7:	0f 29 84 24 60 07 00 	movaps %xmm0,0x760(%rsp)
   4c5de:	00 
   4c5df:	f2 0f 10 47 50       	movsd  0x50(%rdi),%xmm0
   4c5e4:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c5e7:	0f 29 84 24 50 07 00 	movaps %xmm0,0x750(%rsp)
   4c5ee:	00 
   4c5ef:	f2 0f 10 47 58       	movsd  0x58(%rdi),%xmm0
   4c5f4:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c5f7:	0f 29 84 24 40 07 00 	movaps %xmm0,0x740(%rsp)
   4c5fe:	00 
   4c5ff:	f2 0f 10 47 60       	movsd  0x60(%rdi),%xmm0
   4c604:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c607:	0f 29 84 24 30 07 00 	movaps %xmm0,0x730(%rsp)
   4c60e:	00 
   4c60f:	f2 0f 10 47 68       	movsd  0x68(%rdi),%xmm0
   4c614:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c617:	0f 29 84 24 20 07 00 	movaps %xmm0,0x720(%rsp)
   4c61e:	00 
   4c61f:	f2 0f 10 47 70       	movsd  0x70(%rdi),%xmm0
   4c624:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c627:	0f 29 84 24 10 07 00 	movaps %xmm0,0x710(%rsp)
   4c62e:	00 
   4c62f:	f2 0f 10 47 78       	movsd  0x78(%rdi),%xmm0
   4c634:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c637:	0f 29 84 24 00 07 00 	movaps %xmm0,0x700(%rsp)
   4c63e:	00 
   4c63f:	f2 0f 10 87 80 00 00 	movsd  0x80(%rdi),%xmm0
   4c646:	00 
   4c647:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c64a:	0f 29 84 24 f0 06 00 	movaps %xmm0,0x6f0(%rsp)
   4c651:	00 
   4c652:	f2 0f 10 87 88 00 00 	movsd  0x88(%rdi),%xmm0
   4c659:	00 
   4c65a:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c65d:	0f 29 84 24 e0 06 00 	movaps %xmm0,0x6e0(%rsp)
   4c664:	00 
   4c665:	f2 0f 10 87 90 00 00 	movsd  0x90(%rdi),%xmm0
   4c66c:	00 
   4c66d:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c670:	0f 29 84 24 d0 06 00 	movaps %xmm0,0x6d0(%rsp)
   4c677:	00 
   4c678:	f2 0f 10 87 98 00 00 	movsd  0x98(%rdi),%xmm0
   4c67f:	00 
   4c680:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c683:	0f 29 84 24 c0 06 00 	movaps %xmm0,0x6c0(%rsp)
   4c68a:	00 
   4c68b:	f2 0f 10 87 a0 00 00 	movsd  0xa0(%rdi),%xmm0
   4c692:	00 
   4c693:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c696:	0f 29 84 24 b0 06 00 	movaps %xmm0,0x6b0(%rsp)
   4c69d:	00 
   4c69e:	f2 0f 10 87 a8 00 00 	movsd  0xa8(%rdi),%xmm0
   4c6a5:	00 
   4c6a6:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c6a9:	0f 29 84 24 a0 06 00 	movaps %xmm0,0x6a0(%rsp)
   4c6b0:	00 
   4c6b1:	f2 0f 10 87 b0 00 00 	movsd  0xb0(%rdi),%xmm0
   4c6b8:	00 
   4c6b9:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c6bc:	0f 29 84 24 90 06 00 	movaps %xmm0,0x690(%rsp)
   4c6c3:	00 
   4c6c4:	f2 0f 10 87 b8 00 00 	movsd  0xb8(%rdi),%xmm0
   4c6cb:	00 
   4c6cc:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c6cf:	0f 29 84 24 80 06 00 	movaps %xmm0,0x680(%rsp)
   4c6d6:	00 
   4c6d7:	f2 0f 10 87 c0 00 00 	movsd  0xc0(%rdi),%xmm0
   4c6de:	00 
   4c6df:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c6e2:	0f 29 84 24 70 06 00 	movaps %xmm0,0x670(%rsp)
   4c6e9:	00 
   4c6ea:	f2 0f 10 87 c8 00 00 	movsd  0xc8(%rdi),%xmm0
   4c6f1:	00 
   4c6f2:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c6f5:	0f 29 84 24 60 06 00 	movaps %xmm0,0x660(%rsp)
   4c6fc:	00 
   4c6fd:	f2 0f 10 87 d0 00 00 	movsd  0xd0(%rdi),%xmm0
   4c704:	00 
   4c705:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c708:	0f 29 84 24 50 06 00 	movaps %xmm0,0x650(%rsp)
   4c70f:	00 
   4c710:	f2 0f 10 87 d8 00 00 	movsd  0xd8(%rdi),%xmm0
   4c717:	00 
   4c718:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c71b:	0f 29 84 24 40 06 00 	movaps %xmm0,0x640(%rsp)
   4c722:	00 
   4c723:	f2 0f 10 87 e0 00 00 	movsd  0xe0(%rdi),%xmm0
   4c72a:	00 
   4c72b:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c72e:	0f 29 84 24 30 06 00 	movaps %xmm0,0x630(%rsp)
   4c735:	00 
   4c736:	f2 0f 10 87 e8 00 00 	movsd  0xe8(%rdi),%xmm0
   4c73d:	00 
   4c73e:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c741:	0f 29 84 24 20 06 00 	movaps %xmm0,0x620(%rsp)
   4c748:	00 
   4c749:	f2 0f 10 87 f0 00 00 	movsd  0xf0(%rdi),%xmm0
   4c750:	00 
   4c751:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c754:	0f 29 84 24 10 06 00 	movaps %xmm0,0x610(%rsp)
   4c75b:	00 
   4c75c:	f2 0f 10 87 f8 00 00 	movsd  0xf8(%rdi),%xmm0
   4c763:	00 
   4c764:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c767:	0f 29 84 24 00 06 00 	movaps %xmm0,0x600(%rsp)
   4c76e:	00 
   4c76f:	f2 0f 10 87 00 01 00 	movsd  0x100(%rdi),%xmm0
   4c776:	00 
   4c777:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c77a:	0f 29 84 24 f0 05 00 	movaps %xmm0,0x5f0(%rsp)
   4c781:	00 
   4c782:	f2 0f 10 87 08 01 00 	movsd  0x108(%rdi),%xmm0
   4c789:	00 
   4c78a:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c78d:	0f 29 84 24 e0 05 00 	movaps %xmm0,0x5e0(%rsp)
   4c794:	00 
   4c795:	f2 0f 10 87 10 01 00 	movsd  0x110(%rdi),%xmm0
   4c79c:	00 
   4c79d:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c7a0:	0f 29 84 24 d0 05 00 	movaps %xmm0,0x5d0(%rsp)
   4c7a7:	00 
   4c7a8:	f2 0f 10 87 18 01 00 	movsd  0x118(%rdi),%xmm0
   4c7af:	00 
   4c7b0:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c7b3:	0f 29 84 24 c0 05 00 	movaps %xmm0,0x5c0(%rsp)
   4c7ba:	00 
   4c7bb:	f2 0f 10 87 20 01 00 	movsd  0x120(%rdi),%xmm0
   4c7c2:	00 
   4c7c3:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c7c6:	0f 29 84 24 b0 05 00 	movaps %xmm0,0x5b0(%rsp)
   4c7cd:	00 
   4c7ce:	f2 0f 10 87 28 01 00 	movsd  0x128(%rdi),%xmm0
   4c7d5:	00 
   4c7d6:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c7d9:	0f 29 84 24 a0 05 00 	movaps %xmm0,0x5a0(%rsp)
   4c7e0:	00 
   4c7e1:	f2 0f 10 87 30 01 00 	movsd  0x130(%rdi),%xmm0
   4c7e8:	00 
   4c7e9:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c7ec:	0f 29 84 24 90 05 00 	movaps %xmm0,0x590(%rsp)
   4c7f3:	00 
   4c7f4:	f2 0f 10 87 38 01 00 	movsd  0x138(%rdi),%xmm0
   4c7fb:	00 
   4c7fc:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c7ff:	0f 29 84 24 80 05 00 	movaps %xmm0,0x580(%rsp)
   4c806:	00 
   4c807:	f2 0f 10 87 40 01 00 	movsd  0x140(%rdi),%xmm0
   4c80e:	00 
   4c80f:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c812:	0f 29 84 24 70 05 00 	movaps %xmm0,0x570(%rsp)
   4c819:	00 
   4c81a:	f2 0f 10 87 48 01 00 	movsd  0x148(%rdi),%xmm0
   4c821:	00 
   4c822:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c825:	0f 29 84 24 60 05 00 	movaps %xmm0,0x560(%rsp)
   4c82c:	00 
   4c82d:	f2 0f 10 87 50 01 00 	movsd  0x150(%rdi),%xmm0
   4c834:	00 
   4c835:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c838:	0f 29 84 24 50 05 00 	movaps %xmm0,0x550(%rsp)
   4c83f:	00 
   4c840:	f2 0f 10 87 58 01 00 	movsd  0x158(%rdi),%xmm0
   4c847:	00 
   4c848:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c84b:	0f 29 84 24 40 05 00 	movaps %xmm0,0x540(%rsp)
   4c852:	00 
   4c853:	f2 0f 10 87 60 01 00 	movsd  0x160(%rdi),%xmm0
   4c85a:	00 
   4c85b:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c85e:	0f 29 84 24 30 05 00 	movaps %xmm0,0x530(%rsp)
   4c865:	00 
   4c866:	f2 0f 10 87 68 01 00 	movsd  0x168(%rdi),%xmm0
   4c86d:	00 
   4c86e:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c871:	0f 29 84 24 20 05 00 	movaps %xmm0,0x520(%rsp)
   4c878:	00 
   4c879:	f2 0f 10 87 70 01 00 	movsd  0x170(%rdi),%xmm0
   4c880:	00 
   4c881:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c884:	0f 29 84 24 10 05 00 	movaps %xmm0,0x510(%rsp)
   4c88b:	00 
   4c88c:	f2 0f 10 87 78 01 00 	movsd  0x178(%rdi),%xmm0
   4c893:	00 
   4c894:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c897:	0f 29 84 24 00 05 00 	movaps %xmm0,0x500(%rsp)
   4c89e:	00 
   4c89f:	f2 0f 10 87 80 01 00 	movsd  0x180(%rdi),%xmm0
   4c8a6:	00 
   4c8a7:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c8aa:	0f 29 84 24 f0 04 00 	movaps %xmm0,0x4f0(%rsp)
   4c8b1:	00 
   4c8b2:	f2 0f 10 87 88 01 00 	movsd  0x188(%rdi),%xmm0
   4c8b9:	00 
   4c8ba:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c8bd:	0f 29 84 24 e0 04 00 	movaps %xmm0,0x4e0(%rsp)
   4c8c4:	00 
   4c8c5:	f2 0f 10 87 90 01 00 	movsd  0x190(%rdi),%xmm0
   4c8cc:	00 
   4c8cd:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c8d0:	0f 29 84 24 d0 04 00 	movaps %xmm0,0x4d0(%rsp)
   4c8d7:	00 
   4c8d8:	f2 0f 10 87 98 01 00 	movsd  0x198(%rdi),%xmm0
   4c8df:	00 
   4c8e0:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c8e3:	0f 29 84 24 c0 04 00 	movaps %xmm0,0x4c0(%rsp)
   4c8ea:	00 
   4c8eb:	f2 0f 10 87 a0 01 00 	movsd  0x1a0(%rdi),%xmm0
   4c8f2:	00 
   4c8f3:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c8f6:	0f 29 84 24 b0 04 00 	movaps %xmm0,0x4b0(%rsp)
   4c8fd:	00 
   4c8fe:	f2 0f 10 87 a8 01 00 	movsd  0x1a8(%rdi),%xmm0
   4c905:	00 
   4c906:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c909:	0f 29 84 24 a0 04 00 	movaps %xmm0,0x4a0(%rsp)
   4c910:	00 
   4c911:	f2 0f 10 87 b0 01 00 	movsd  0x1b0(%rdi),%xmm0
   4c918:	00 
   4c919:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c91c:	0f 29 84 24 90 04 00 	movaps %xmm0,0x490(%rsp)
   4c923:	00 
   4c924:	f2 0f 10 87 b8 01 00 	movsd  0x1b8(%rdi),%xmm0
   4c92b:	00 
   4c92c:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c92f:	0f 29 84 24 80 04 00 	movaps %xmm0,0x480(%rsp)
   4c936:	00 
   4c937:	f2 0f 10 87 c0 01 00 	movsd  0x1c0(%rdi),%xmm0
   4c93e:	00 
   4c93f:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c942:	0f 29 84 24 70 04 00 	movaps %xmm0,0x470(%rsp)
   4c949:	00 
   4c94a:	f2 0f 10 87 c8 01 00 	movsd  0x1c8(%rdi),%xmm0
   4c951:	00 
   4c952:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c955:	0f 29 84 24 60 04 00 	movaps %xmm0,0x460(%rsp)
   4c95c:	00 
   4c95d:	f2 0f 10 87 d0 01 00 	movsd  0x1d0(%rdi),%xmm0
   4c964:	00 
   4c965:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c968:	0f 29 84 24 50 04 00 	movaps %xmm0,0x450(%rsp)
   4c96f:	00 
   4c970:	f2 0f 10 87 d8 01 00 	movsd  0x1d8(%rdi),%xmm0
   4c977:	00 
   4c978:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c97b:	0f 29 84 24 40 04 00 	movaps %xmm0,0x440(%rsp)
   4c982:	00 
   4c983:	f2 0f 10 87 e0 01 00 	movsd  0x1e0(%rdi),%xmm0
   4c98a:	00 
   4c98b:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c98e:	0f 29 84 24 30 04 00 	movaps %xmm0,0x430(%rsp)
   4c995:	00 
   4c996:	f2 0f 10 87 e8 01 00 	movsd  0x1e8(%rdi),%xmm0
   4c99d:	00 
   4c99e:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c9a1:	0f 29 84 24 20 04 00 	movaps %xmm0,0x420(%rsp)
   4c9a8:	00 
   4c9a9:	f2 0f 10 87 f0 01 00 	movsd  0x1f0(%rdi),%xmm0
   4c9b0:	00 
   4c9b1:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c9b4:	0f 29 84 24 10 04 00 	movaps %xmm0,0x410(%rsp)
   4c9bb:	00 
   4c9bc:	f2 0f 10 87 f8 01 00 	movsd  0x1f8(%rdi),%xmm0
   4c9c3:	00 
   4c9c4:	0f 16 c0             	movlhps %xmm0,%xmm0
   4c9c7:	0f 29 84 24 00 04 00 	movaps %xmm0,0x400(%rsp)
   4c9ce:	00 
   4c9cf:	b9 78 02 00 00       	mov    $0x278,%ecx
   4c9d4:	66 66 66 2e 0f 1f 84 	data16 data16 cs nopw 0x0(%rax,%rax,1)
   4c9db:	00 00 00 00 00 
   4c9e0:	f2 0f 10 54 0f 88    	movsd  -0x78(%rdi,%rcx,1),%xmm2
   4c9e6:	f2 0f 10 44 0f 90    	movsd  -0x70(%rdi,%rcx,1),%xmm0
   4c9ec:	66 0f 16 54 0f c8    	movhpd -0x38(%rdi,%rcx,1),%xmm2
   4c9f2:	66 0f 28 e2          	movapd %xmm2,%xmm4
   4c9f6:	66 0f 59 a4 24 f0 07 	mulpd  0x7f0(%rsp),%xmm4
   4c9fd:	00 00 
   4c9ff:	66 0f 28 ea          	movapd %xmm2,%xmm5
   4ca03:	66 0f 59 ac 24 e0 07 	mulpd  0x7e0(%rsp),%xmm5
   4ca0a:	00 00 
   4ca0c:	66 0f 28 f2          	movapd %xmm2,%xmm6
   4ca10:	66 0f 59 b4 24 d0 07 	mulpd  0x7d0(%rsp),%xmm6
   4ca17:	00 00 
   4ca19:	66 0f 28 fa          	movapd %xmm2,%xmm7
   4ca1d:	66 0f 59 bc 24 c0 07 	mulpd  0x7c0(%rsp),%xmm7
   4ca24:	00 00 
   4ca26:	66 44 0f 28 c2       	movapd %xmm2,%xmm8
   4ca2b:	66 44 0f 59 84 24 b0 	mulpd  0x7b0(%rsp),%xmm8
   4ca32:	07 00 00 
   4ca35:	66 44 0f 28 ca       	movapd %xmm2,%xmm9
   4ca3a:	66 44 0f 59 8c 24 a0 	mulpd  0x7a0(%rsp),%xmm9
   4ca41:	07 00 00 
   4ca44:	66 0f 28 ca          	movapd %xmm2,%xmm1
   4ca48:	66 0f 59 8c 24 90 07 	mulpd  0x790(%rsp),%xmm1
   4ca4f:	00 00 
   4ca51:	66 0f 16 44 0f d0    	movhpd -0x30(%rdi,%rcx,1),%xmm0
   4ca57:	66 0f 28 d8          	movapd %xmm0,%xmm3
   4ca5b:	66 0f 59 9c 24 70 07 	mulpd  0x770(%rsp),%xmm3
   4ca62:	00 00 
   4ca64:	66 0f 58 dc          	addpd  %xmm4,%xmm3
   4ca68:	66 0f 28 e0          	movapd %xmm0,%xmm4
   4ca6c:	66 0f 59 a4 24 60 07 	mulpd  0x760(%rsp),%xmm4
   4ca73:	00 00 
   4ca75:	66 0f 58 e5          	addpd  %xmm5,%xmm4
   4ca79:	66 0f 28 e8          	movapd %xmm0,%xmm5
   4ca7d:	66 0f 59 ac 24 50 07 	mulpd  0x750(%rsp),%xmm5
   4ca84:	00 00 
   4ca86:	66 0f 58 ee          	addpd  %xmm6,%xmm5
   4ca8a:	66 0f 28 f0          	movapd %xmm0,%xmm6
   4ca8e:	66 0f 59 b4 24 40 07 	mulpd  0x740(%rsp),%xmm6
   4ca95:	00 00 
   4ca97:	66 0f 58 f7          	addpd  %xmm7,%xmm6
   4ca9b:	66 0f 28 f8          	movapd %xmm0,%xmm7
   4ca9f:	66 0f 59 bc 24 30 07 	mulpd  0x730(%rsp),%xmm7
   4caa6:	00 00 
   4caa8:	66 41 0f 58 f8       	addpd  %xmm8,%xmm7
   4caad:	66 44 0f 28 c0       	movapd %xmm0,%xmm8
   4cab2:	66 44 0f 59 84 24 20 	mulpd  0x720(%rsp),%xmm8
   4cab9:	07 00 00 
   4cabc:	66 45 0f 58 c1       	addpd  %xmm9,%xmm8
   4cac1:	66 44 0f 28 c8       	movapd %xmm0,%xmm9
   4cac6:	66 44 0f 59 8c 24 10 	mulpd  0x710(%rsp),%xmm9
   4cacd:	07 00 00 
   4cad0:	66 44 0f 58 c9       	addpd  %xmm1,%xmm9
   4cad5:	f2 0f 10 4c 0f 98    	movsd  -0x68(%rdi,%rcx,1),%xmm1
   4cadb:	66 0f 59 94 24 80 07 	mulpd  0x780(%rsp),%xmm2
   4cae2:	00 00 
   4cae4:	66 0f 59 84 24 00 07 	mulpd  0x700(%rsp),%xmm0
   4caeb:	00 00 
   4caed:	66 0f 16 4c 0f d8    	movhpd -0x28(%rdi,%rcx,1),%xmm1
   4caf3:	66 0f 58 c2          	addpd  %xmm2,%xmm0
   4caf7:	66 44 0f 28 d1       	movapd %xmm1,%xmm10
   4cafc:	66 44 0f 59 94 24 f0 	mulpd  0x6f0(%rsp),%xmm10
   4cb03:	06 00 00 
   4cb06:	66 44 0f 58 d3       	addpd  %xmm3,%xmm10
   4cb0b:	66 44 0f 28 d9       	movapd %xmm1,%xmm11
   4cb10:	66 44 0f 59 9c 24 e0 	mulpd  0x6e0(%rsp),%xmm11
   4cb17:	06 00 00 
   4cb1a:	66 44 0f 58 dc       	addpd  %xmm4,%xmm11
   4cb1f:	66 44 0f 28 e1       	movapd %xmm1,%xmm12
   4cb24:	66 44 0f 59 a4 24 d0 	mulpd  0x6d0(%rsp),%xmm12
   4cb2b:	06 00 00 
   4cb2e:	66 44 0f 58 e5       	addpd  %xmm5,%xmm12
   4cb33:	66 44 0f 28 e9       	movapd %xmm1,%xmm13
   4cb38:	66 44 0f 59 ac 24 c0 	mulpd  0x6c0(%rsp),%xmm13
   4cb3f:	06 00 00 
   4cb42:	66 44 0f 58 ee       	addpd  %xmm6,%xmm13
   4cb47:	66 44 0f 28 f1       	movapd %xmm1,%xmm14
   4cb4c:	66 44 0f 59 b4 24 b0 	mulpd  0x6b0(%rsp),%xmm14
   4cb53:	06 00 00 
   4cb56:	66 44 0f 58 f7       	addpd  %xmm7,%xmm14
   4cb5b:	66 44 0f 28 f9       	movapd %xmm1,%xmm15
   4cb60:	66 44 0f 59 bc 24 a0 	mulpd  0x6a0(%rsp),%xmm15
   4cb67:	06 00 00 
   4cb6a:	66 45 0f 58 f8       	addpd  %xmm8,%xmm15
   4cb6f:	66 0f 28 d9          	movapd %xmm1,%xmm3
   4cb73:	66 0f 59 9c 24 90 06 	mulpd  0x690(%rsp),%xmm3
   4cb7a:	00 00 
   4cb7c:	66 41 0f 58 d9       	addpd  %xmm9,%xmm3
   4cb81:	f2 0f 10 54 0f a0    	movsd  -0x60(%rdi,%rcx,1),%xmm2
   4cb87:	66 0f 59 8c 24 80 06 	mulpd  0x680(%rsp),%xmm1
   4cb8e:	00 00 
   4cb90:	66 0f 58 c8          	addpd  %xmm0,%xmm1
   4cb94:	66 0f 16 54 0f e0    	movhpd -0x20(%rdi,%rcx,1),%xmm2
   4cb9a:	66 0f 28 c2          	movapd %xmm2,%xmm0
   4cb9e:	66 0f 59 84 24 70 06 	mulpd  0x670(%rsp),%xmm0
   4cba5:	00 00 
   4cba7:	66 41 0f 58 c2       	addpd  %xmm10,%xmm0
   4cbac:	66 0f 29 84 24 00 08 	movapd %xmm0,0x800(%rsp)
   4cbb3:	00 00 
   4cbb5:	66 0f 28 e2          	movapd %xmm2,%xmm4
   4cbb9:	66 0f 59 a4 24 60 06 	mulpd  0x660(%rsp),%xmm4
   4cbc0:	00 00 
   4cbc2:	66 41 0f 58 e3       	addpd  %xmm11,%xmm4
   4cbc7:	66 0f 28 ea          	movapd %xmm2,%xmm5
   4cbcb:	66 0f 59 ac 24 50 06 	mulpd  0x650(%rsp),%xmm5
   4cbd2:	00 00 
   4cbd4:	66 41 0f 58 ec       	addpd  %xmm12,%xmm5
   4cbd9:	66 0f 28 f2          	movapd %xmm2,%xmm6
   4cbdd:	66 0f 59 b4 24 40 06 	mulpd  0x640(%rsp),%xmm6
   4cbe4:	00 00 
   4cbe6:	66 41 0f 58 f5       	addpd  %xmm13,%xmm6
   4cbeb:	66 0f 28 fa          	movapd %xmm2,%xmm7
   4cbef:	66 0f 59 bc 24 30 06 	mulpd  0x630(%rsp),%xmm7
   4cbf6:	00 00 
   4cbf8:	66 41 0f 58 fe       	addpd  %xmm14,%xmm7
   4cbfd:	66 44 0f 28 c2       	movapd %xmm2,%xmm8
   4cc02:	66 44 0f 59 84 24 20 	mulpd  0x620(%rsp),%xmm8
   4cc09:	06 00 00 
   4cc0c:	66 45 0f 58 c7       	addpd  %xmm15,%xmm8
   4cc11:	66 44 0f 28 ca       	movapd %xmm2,%xmm9
   4cc16:	66 44 0f 59 8c 24 10 	mulpd  0x610(%rsp),%xmm9
   4cc1d:	06 00 00 
   4cc20:	66 44 0f 58 cb       	addpd  %xmm3,%xmm9
   4cc25:	66 0f 59 94 24 00 06 	mulpd  0x600(%rsp),%xmm2
   4cc2c:	00 00 
   4cc2e:	f2 0f 10 44 0f a8    	movsd  -0x58(%rdi,%rcx,1),%xmm0
   4cc34:	66 0f 16 44 0f e8    	movhpd -0x18(%rdi,%rcx,1),%xmm0
   4cc3a:	66 0f 58 d1          	addpd  %xmm1,%xmm2
   4cc3e:	66 44 0f 28 d0       	movapd %xmm0,%xmm10
   4cc43:	66 44 0f 59 94 24 f0 	mulpd  0x5f0(%rsp),%xmm10
   4cc4a:	05 00 00 
   4cc4d:	66 44 0f 58 94 24 00 	addpd  0x800(%rsp),%xmm10
   4cc54:	08 00 00 
   4cc57:	66 44 0f 28 d8       	movapd %xmm0,%xmm11
   4cc5c:	66 44 0f 59 9c 24 e0 	mulpd  0x5e0(%rsp),%xmm11
   4cc63:	05 00 00 
   4cc66:	66 44 0f 58 dc       	addpd  %xmm4,%xmm11
   4cc6b:	66 44 0f 28 e0       	movapd %xmm0,%xmm12
   4cc70:	66 44 0f 59 a4 24 d0 	mulpd  0x5d0(%rsp),%xmm12
   4cc77:	05 00 00 
   4cc7a:	66 44 0f 58 e5       	addpd  %xmm5,%xmm12
   4cc7f:	66 44 0f 28 e8       	movapd %xmm0,%xmm13
   4cc84:	66 44 0f 59 ac 24 c0 	mulpd  0x5c0(%rsp),%xmm13
   4cc8b:	05 00 00 
   4cc8e:	66 44 0f 58 ee       	addpd  %xmm6,%xmm13
   4cc93:	66 44 0f 28 f0       	movapd %xmm0,%xmm14
   4cc98:	66 44 0f 59 b4 24 b0 	mulpd  0x5b0(%rsp),%xmm14
   4cc9f:	05 00 00 
   4cca2:	66 44 0f 58 f7       	addpd  %xmm7,%xmm14
   4cca7:	66 44 0f 28 f8       	movapd %xmm0,%xmm15
   4ccac:	66 44 0f 59 bc 24 a0 	mulpd  0x5a0(%rsp),%xmm15
   4ccb3:	05 00 00 
   4ccb6:	66 45 0f 58 f8       	addpd  %xmm8,%xmm15
   4ccbb:	66 0f 28 d8          	movapd %xmm0,%xmm3
   4ccbf:	66 0f 59 9c 24 90 05 	mulpd  0x590(%rsp),%xmm3
   4ccc6:	00 00 
   4ccc8:	66 41 0f 58 d9       	addpd  %xmm9,%xmm3
   4cccd:	66 0f 59 84 24 80 05 	mulpd  0x580(%rsp),%xmm0
   4ccd4:	00 00 
   4ccd6:	66 0f 58 c2          	addpd  %xmm2,%xmm0
   4ccda:	f2 0f 10 4c 0f b0    	movsd  -0x50(%rdi,%rcx,1),%xmm1
   4cce0:	66 0f 16 4c 0f f0    	movhpd -0x10(%rdi,%rcx,1),%xmm1
   4cce6:	66 0f 28 d1          	movapd %xmm1,%xmm2
   4ccea:	66 0f 59 94 24 70 05 	mulpd  0x570(%rsp),%xmm2
   4ccf1:	00 00 
   4ccf3:	66 41 0f 58 d2       	addpd  %xmm10,%xmm2
   4ccf8:	66 44 0f 28 d2       	movapd %xmm2,%xmm10
   4ccfd:	66 0f 28 e1          	movapd %xmm1,%xmm4
   4cd01:	66 0f 59 a4 24 60 05 	mulpd  0x560(%rsp),%xmm4
   4cd08:	00 00 
   4cd0a:	66 41 0f 58 e3       	addpd  %xmm11,%xmm4
   4cd0f:	66 0f 28 e9          	movapd %xmm1,%xmm5
   4cd13:	66 0f 59 ac 24 50 05 	mulpd  0x550(%rsp),%xmm5
   4cd1a:	00 00 
   4cd1c:	66 41 0f 58 ec       	addpd  %xmm12,%xmm5
   4cd21:	66 0f 28 f1          	movapd %xmm1,%xmm6
   4cd25:	66 0f 59 b4 24 40 05 	mulpd  0x540(%rsp),%xmm6
   4cd2c:	00 00 
   4cd2e:	66 41 0f 58 f5       	addpd  %xmm13,%xmm6
   4cd33:	66 0f 28 f9          	movapd %xmm1,%xmm7
   4cd37:	66 0f 59 bc 24 30 05 	mulpd  0x530(%rsp),%xmm7
   4cd3e:	00 00 
   4cd40:	66 41 0f 58 fe       	addpd  %xmm14,%xmm7
   4cd45:	66 44 0f 28 c1       	movapd %xmm1,%xmm8
   4cd4a:	66 44 0f 59 84 24 20 	mulpd  0x520(%rsp),%xmm8
   4cd51:	05 00 00 
   4cd54:	66 45 0f 58 c7       	addpd  %xmm15,%xmm8
   4cd59:	66 44 0f 28 c9       	movapd %xmm1,%xmm9
   4cd5e:	66 44 0f 59 8c 24 10 	mulpd  0x510(%rsp),%xmm9
   4cd65:	05 00 00 
   4cd68:	66 44 0f 58 cb       	addpd  %xmm3,%xmm9
   4cd6d:	66 0f 59 8c 24 00 05 	mulpd  0x500(%rsp),%xmm1
   4cd74:	00 00 
   4cd76:	f2 0f 10 54 0f b8    	movsd  -0x48(%rdi,%rcx,1),%xmm2
   4cd7c:	66 0f 16 54 0f f8    	movhpd -0x8(%rdi,%rcx,1),%xmm2
   4cd82:	66 0f 58 c8          	addpd  %xmm0,%xmm1
   4cd86:	66 0f 28 da          	movapd %xmm2,%xmm3
   4cd8a:	66 0f 59 9c 24 f0 04 	mulpd  0x4f0(%rsp),%xmm3
   4cd91:	00 00 
   4cd93:	66 41 0f 58 da       	addpd  %xmm10,%xmm3
   4cd98:	66 44 0f 28 d2       	movapd %xmm2,%xmm10
   4cd9d:	66 44 0f 59 94 24 e0 	mulpd  0x4e0(%rsp),%xmm10
   4cda4:	04 00 00 
   4cda7:	66 44 0f 58 d4       	addpd  %xmm4,%xmm10
   4cdac:	66 44 0f 28 da       	movapd %xmm2,%xmm11
   4cdb1:	66 44 0f 59 9c 24 d0 	mulpd  0x4d0(%rsp),%xmm11
   4cdb8:	04 00 00 
   4cdbb:	66 44 0f 58 dd       	addpd  %xmm5,%xmm11
   4cdc0:	66 0f 28 ea          	movapd %xmm2,%xmm5
   4cdc4:	66 0f 59 ac 24 c0 04 	mulpd  0x4c0(%rsp),%xmm5
   4cdcb:	00 00 
   4cdcd:	66 0f 58 ee          	addpd  %xmm6,%xmm5
   4cdd1:	66 0f 28 f2          	movapd %xmm2,%xmm6
   4cdd5:	66 0f 59 b4 24 b0 04 	mulpd  0x4b0(%rsp),%xmm6
   4cddc:	00 00 
   4cdde:	66 0f 58 f7          	addpd  %xmm7,%xmm6
   4cde2:	66 0f 28 fa          	movapd %xmm2,%xmm7
   4cde6:	66 0f 59 bc 24 a0 04 	mulpd  0x4a0(%rsp),%xmm7
   4cded:	00 00 
   4cdef:	66 41 0f 58 f8       	addpd  %xmm8,%xmm7
   4cdf4:	66 44 0f 28 c2       	movapd %xmm2,%xmm8
   4cdf9:	66 44 0f 59 84 24 90 	mulpd  0x490(%rsp),%xmm8
   4ce00:	04 00 00 
   4ce03:	66 45 0f 58 c1       	addpd  %xmm9,%xmm8
   4ce08:	66 0f 59 94 24 80 04 	mulpd  0x480(%rsp),%xmm2
   4ce0f:	00 00 
   4ce11:	66 0f 58 d1          	addpd  %xmm1,%xmm2
   4ce15:	f2 0f 10 44 0f c0    	movsd  -0x40(%rdi,%rcx,1),%xmm0
   4ce1b:	66 0f 16 04 0f       	movhpd (%rdi,%rcx,1),%xmm0
   4ce20:	66 0f 28 e0          	movapd %xmm0,%xmm4
   4ce24:	66 0f 59 a4 24 70 04 	mulpd  0x470(%rsp),%xmm4
   4ce2b:	00 00 
   4ce2d:	66 0f 58 e3          	addpd  %xmm3,%xmm4
   4ce31:	66 0f 28 c8          	movapd %xmm0,%xmm1
   4ce35:	66 0f 59 8c 24 60 04 	mulpd  0x460(%rsp),%xmm1
   4ce3c:	00 00 
   4ce3e:	66 41 0f 58 ca       	addpd  %xmm10,%xmm1
   4ce43:	66 44 0f 28 c8       	movapd %xmm0,%xmm9
   4ce48:	66 44 0f 59 8c 24 50 	mulpd  0x450(%rsp),%xmm9
   4ce4f:	04 00 00 
   4ce52:	66 45 0f 58 cb       	addpd  %xmm11,%xmm9
   4ce57:	66 0f 28 d8          	movapd %xmm0,%xmm3
   4ce5b:	66 0f 59 9c 24 40 04 	mulpd  0x440(%rsp),%xmm3
   4ce62:	00 00 
   4ce64:	66 0f 58 dd          	addpd  %xmm5,%xmm3
   4ce68:	66 44 0f 28 d0       	movapd %xmm0,%xmm10
   4ce6d:	66 44 0f 59 94 24 30 	mulpd  0x430(%rsp),%xmm10
   4ce74:	04 00 00 
   4ce77:	66 44 0f 58 d6       	addpd  %xmm6,%xmm10
   4ce7c:	66 0f 28 e8          	movapd %xmm0,%xmm5
   4ce80:	66 0f 59 ac 24 20 04 	mulpd  0x420(%rsp),%xmm5
   4ce87:	00 00 
   4ce89:	66 0f 58 ef          	addpd  %xmm7,%xmm5
   4ce8d:	66 0f 28 f0          	movapd %xmm0,%xmm6
   4ce91:	66 0f 59 b4 24 10 04 	mulpd  0x410(%rsp),%xmm6
   4ce98:	00 00 
   4ce9a:	66 41 0f 58 f0       	addpd  %xmm8,%xmm6
   4ce9f:	66 0f 59 84 24 00 04 	mulpd  0x400(%rsp),%xmm0
   4cea6:	00 00 
   4cea8:	66 0f 58 c2          	addpd  %xmm2,%xmm0
   4ceac:	66 0f 28 d4          	movapd %xmm4,%xmm2
   4ceb0:	66 0f 14 d1          	unpcklpd %xmm1,%xmm2
   4ceb4:	0f 12 cc             	movhlps %xmm4,%xmm1
   4ceb7:	66 41 0f 28 e1       	movapd %xmm9,%xmm4
   4cebc:	66 0f 14 e3          	unpcklpd %xmm3,%xmm4
   4cec0:	41 0f 12 d9          	movhlps %xmm9,%xmm3
   4cec4:	66 41 0f 28 fa       	movapd %xmm10,%xmm7
   4cec9:	66 0f 14 fd          	unpcklpd %xmm5,%xmm7
   4cecd:	41 0f 12 ea          	movhlps %xmm10,%xmm5
   4ced1:	66 44 0f 28 c6       	movapd %xmm6,%xmm8
   4ced6:	66 44 0f 14 c0       	unpcklpd %xmm0,%xmm8
   4cedb:	0f 12 c6             	movhlps %xmm6,%xmm0
   4cede:	0f 29 84 0c 08 06 00 	movaps %xmm0,0x608(%rsp,%rcx,1)
   4cee5:	00 
   4cee6:	0f 29 ac 0c f8 05 00 	movaps %xmm5,0x5f8(%rsp,%rcx,1)
   4ceed:	00 
   4ceee:	0f 29 9c 0c e8 05 00 	movaps %xmm3,0x5e8(%rsp,%rcx,1)
   4cef5:	00 
   4cef6:	0f 29 8c 0c d8 05 00 	movaps %xmm1,0x5d8(%rsp,%rcx,1)
   4cefd:	00 
   4cefe:	66 44 0f 29 84 0c c8 	movapd %xmm8,0x5c8(%rsp,%rcx,1)
   4cf05:	05 00 00 
   4cf08:	66 0f 29 bc 0c b8 05 	movapd %xmm7,0x5b8(%rsp,%rcx,1)
   4cf0f:	00 00 
   4cf11:	66 0f 29 a4 0c a8 05 	movapd %xmm4,0x5a8(%rsp,%rcx,1)
   4cf18:	00 00 
   4cf1a:	66 0f 29 94 0c 98 05 	movapd %xmm2,0x598(%rsp,%rcx,1)
   4cf21:	00 00 
   4cf23:	48 83 e9 80          	sub    $0xffffffffffffff80,%rcx
   4cf27:	48 81 f9 78 04 00 00 	cmp    $0x478,%rcx
   4cf2e:	0f 85 ac fa ff ff    	jne    4c9e0 <<adaptive_threadpool_thesis::workload::compute_heavy::ComputeHeavyWorkload as adaptive_threadpool_thesis::workload::Workload>::execute+0x720>
   4cf34:	66 0f 28 ac 24 60 01 	movapd 0x160(%rsp),%xmm5
   4cf3b:	00 00 
   4cf3d:	66 0f 58 ac 24 20 08 	addpd  0x820(%rsp),%xmm5
   4cf44:	00 00 
   4cf46:	66 0f 28 04 24       	movapd (%rsp),%xmm0
   4cf4b:	66 0f 58 84 24 30 08 	addpd  0x830(%rsp),%xmm0
   4cf52:	00 00 
   4cf54:	66 0f 29 04 24       	movapd %xmm0,(%rsp)
   4cf59:	66 0f 28 84 24 f0 03 	movapd 0x3f0(%rsp),%xmm0
   4cf60:	00 00 
   4cf62:	66 0f 58 84 24 40 08 	addpd  0x840(%rsp),%xmm0
   4cf69:	00 00 
   4cf6b:	66 0f 28 f8          	movapd %xmm0,%xmm7
   4cf6f:	66 0f 28 84 24 e0 03 	movapd 0x3e0(%rsp),%xmm0
   4cf76:	00 00 
   4cf78:	66 0f 58 84 24 50 08 	addpd  0x850(%rsp),%xmm0
   4cf7f:	00 00 
   4cf81:	66 44 0f 28 c0       	movapd %xmm0,%xmm8
   4cf86:	66 0f 28 b4 24 d0 03 	movapd 0x3d0(%rsp),%xmm6
   4cf8d:	00 00 
   4cf8f:	66 0f 58 b4 24 60 08 	addpd  0x860(%rsp),%xmm6
   4cf96:	00 00 
   4cf98:	66 0f 28 84 24 c0 03 	movapd 0x3c0(%rsp),%xmm0
   4cf9f:	00 00 
   4cfa1:	66 0f 58 84 24 70 08 	addpd  0x870(%rsp),%xmm0
   4cfa8:	00 00 
   4cfaa:	66 44 0f 28 d0       	movapd %xmm0,%xmm10
   4cfaf:	66 0f 28 84 24 b0 03 	movapd 0x3b0(%rsp),%xmm0
   4cfb6:	00 00 
   4cfb8:	66 0f 58 84 24 80 08 	addpd  0x880(%rsp),%xmm0
   4cfbf:	00 00 
   4cfc1:	66 44 0f 28 d8       	movapd %xmm0,%xmm11
   4cfc6:	66 44 0f 28 8c 24 a0 	movapd 0x3a0(%rsp),%xmm9
   4cfcd:	03 00 00 
   4cfd0:	66 44 0f 58 8c 24 90 	addpd  0x890(%rsp),%xmm9
   4cfd7:	08 00 00 
   4cfda:	66 0f 28 84 24 90 03 	movapd 0x390(%rsp),%xmm0
   4cfe1:	00 00 
   4cfe3:	66 0f 58 84 24 a0 08 	addpd  0x8a0(%rsp),%xmm0
   4cfea:	00 00 
   4cfec:	66 44 0f 28 e8       	movapd %xmm0,%xmm13
   4cff1:	66 0f 28 84 24 80 03 	movapd 0x380(%rsp),%xmm0
   4cff8:	00 00 
   4cffa:	66 0f 58 84 24 b0 08 	addpd  0x8b0(%rsp),%xmm0
   4d001:	00 00 
   4d003:	66 44 0f 28 f0       	movapd %xmm0,%xmm14
   4d008:	66 44 0f 28 a4 24 70 	movapd 0x370(%rsp),%xmm12
   4d00f:	03 00 00 
   4d012:	66 44 0f 58 a4 24 c0 	addpd  0x8c0(%rsp),%xmm12
   4d019:	08 00 00 
   4d01c:	66 0f 28 84 24 10 01 	movapd 0x110(%rsp),%xmm0
   4d023:	00 00 
   4d025:	66 0f 58 84 24 d0 08 	addpd  0x8d0(%rsp),%xmm0
   4d02c:	00 00 
   4d02e:	66 0f 29 84 24 10 01 	movapd %xmm0,0x110(%rsp)
   4d035:	00 00 
   4d037:	66 0f 28 84 24 00 01 	movapd 0x100(%rsp),%xmm0
   4d03e:	00 00 
   4d040:	66 0f 58 84 24 e0 08 	addpd  0x8e0(%rsp),%xmm0
   4d047:	00 00 
   4d049:	66 0f 29 84 24 00 01 	movapd %xmm0,0x100(%rsp)
   4d050:	00 00 
   4d052:	66 0f 28 84 24 f0 00 	movapd 0xf0(%rsp),%xmm0
   4d059:	00 00 
   4d05b:	66 0f 58 84 24 f0 08 	addpd  0x8f0(%rsp),%xmm0
   4d062:	00 00 
   4d064:	66 0f 29 84 24 f0 00 	movapd %xmm0,0xf0(%rsp)
   4d06b:	00 00 
   4d06d:	66 0f 28 84 24 e0 00 	movapd 0xe0(%rsp),%xmm0
   4d074:	00 00 
   4d076:	66 0f 58 84 24 00 09 	addpd  0x900(%rsp),%xmm0
   4d07d:	00 00 
   4d07f:	66 0f 29 84 24 e0 00 	movapd %xmm0,0xe0(%rsp)
   4d086:	00 00 
   4d088:	66 0f 28 84 24 d0 00 	movapd 0xd0(%rsp),%xmm0
   4d08f:	00 00 
   4d091:	66 0f 58 84 24 10 09 	addpd  0x910(%rsp),%xmm0
   4d098:	00 00 
   4d09a:	66 0f 29 84 24 d0 00 	movapd %xmm0,0xd0(%rsp)
   4d0a1:	00 00 
   4d0a3:	66 0f 28 84 24 c0 00 	movapd 0xc0(%rsp),%xmm0
   4d0aa:	00 00 
   4d0ac:	66 0f 58 84 24 20 09 	addpd  0x920(%rsp),%xmm0
   4d0b3:	00 00 
   4d0b5:	66 0f 29 84 24 c0 00 	movapd %xmm0,0xc0(%rsp)
   4d0bc:	00 00 
   4d0be:	66 0f 28 84 24 b0 00 	movapd 0xb0(%rsp),%xmm0
   4d0c5:	00 00 
   4d0c7:	66 0f 58 84 24 30 09 	addpd  0x930(%rsp),%xmm0
   4d0ce:	00 00 
   4d0d0:	66 0f 29 84 24 b0 00 	movapd %xmm0,0xb0(%rsp)
   4d0d7:	00 00 
   4d0d9:	66 0f 28 84 24 a0 00 	movapd 0xa0(%rsp),%xmm0
   4d0e0:	00 00 
   4d0e2:	66 0f 58 84 24 40 09 	addpd  0x940(%rsp),%xmm0
   4d0e9:	00 00 
   4d0eb:	66 0f 29 84 24 a0 00 	movapd %xmm0,0xa0(%rsp)
   4d0f2:	00 00 
   4d0f4:	66 0f 28 84 24 90 00 	movapd 0x90(%rsp),%xmm0
   4d0fb:	00 00 
   4d0fd:	66 0f 58 84 24 50 09 	addpd  0x950(%rsp),%xmm0
   4d104:	00 00 
   4d106:	66 0f 29 84 24 90 00 	movapd %xmm0,0x90(%rsp)
   4d10d:	00 00 
   4d10f:	66 0f 28 84 24 80 00 	movapd 0x80(%rsp),%xmm0
   4d116:	00 00 
   4d118:	66 0f 58 84 24 60 09 	addpd  0x960(%rsp),%xmm0
   4d11f:	00 00 
   4d121:	66 0f 29 84 24 80 00 	movapd %xmm0,0x80(%rsp)
   4d128:	00 00 
   4d12a:	66 0f 28 44 24 70    	movapd 0x70(%rsp),%xmm0
   4d130:	66 0f 58 84 24 70 09 	addpd  0x970(%rsp),%xmm0
   4d137:	00 00 
   4d139:	66 0f 29 44 24 70    	movapd %xmm0,0x70(%rsp)
   4d13f:	66 0f 28 44 24 60    	movapd 0x60(%rsp),%xmm0
   4d145:	66 0f 58 84 24 80 09 	addpd  0x980(%rsp),%xmm0
   4d14c:	00 00 
   4d14e:	66 0f 29 44 24 60    	movapd %xmm0,0x60(%rsp)
   4d154:	66 0f 28 44 24 50    	movapd 0x50(%rsp),%xmm0
   4d15a:	66 0f 58 84 24 90 09 	addpd  0x990(%rsp),%xmm0
   4d161:	00 00 
   4d163:	66 0f 29 44 24 50    	movapd %xmm0,0x50(%rsp)
   4d169:	66 0f 28 44 24 40    	movapd 0x40(%rsp),%xmm0
   4d16f:	66 0f 58 84 24 a0 09 	addpd  0x9a0(%rsp),%xmm0
   4d176:	00 00 
   4d178:	66 0f 29 44 24 40    	movapd %xmm0,0x40(%rsp)
   4d17e:	66 0f 28 44 24 30    	movapd 0x30(%rsp),%xmm0
   4d184:	66 0f 58 84 24 b0 09 	addpd  0x9b0(%rsp),%xmm0
   4d18b:	00 00 
   4d18d:	66 0f 29 44 24 30    	movapd %xmm0,0x30(%rsp)
   4d193:	66 0f 28 44 24 20    	movapd 0x20(%rsp),%xmm0
   4d199:	66 0f 58 84 24 c0 09 	addpd  0x9c0(%rsp),%xmm0
   4d1a0:	00 00 
   4d1a2:	66 0f 29 44 24 20    	movapd %xmm0,0x20(%rsp)
   4d1a8:	66 0f 28 44 24 10    	movapd 0x10(%rsp),%xmm0
   4d1ae:	66 0f 58 84 24 d0 09 	addpd  0x9d0(%rsp),%xmm0
   4d1b5:	00 00 
   4d1b7:	66 0f 29 44 24 10    	movapd %xmm0,0x10(%rsp)
   4d1bd:	66 0f 28 a4 24 50 01 	movapd 0x150(%rsp),%xmm4
   4d1c4:	00 00 
   4d1c6:	66 0f 58 a4 24 e0 09 	addpd  0x9e0(%rsp),%xmm4
   4d1cd:	00 00 
   4d1cf:	66 0f 28 9c 24 40 01 	movapd 0x140(%rsp),%xmm3
   4d1d6:	00 00 
   4d1d8:	66 0f 58 9c 24 f0 09 	addpd  0x9f0(%rsp),%xmm3
   4d1df:	00 00 
   4d1e1:	66 0f 6f 84 24 10 08 	movdqa 0x810(%rsp),%xmm0
   4d1e8:	00 00 
   4d1ea:	66 0f 28 94 24 30 01 	movapd 0x130(%rsp),%xmm2
   4d1f1:	00 00 
   4d1f3:	66 0f 58 94 24 00 0a 	addpd  0xa00(%rsp),%xmm2
   4d1fa:	00 00 
   4d1fc:	66 48 0f 7e c1       	movq   %xmm0,%rcx
   4d201:	66 0f 28 8c 24 20 01 	movapd 0x120(%rsp),%xmm1
   4d208:	00 00 
   4d20a:	66 0f 58 c8          	addpd  %xmm0,%xmm1
   4d20e:	66 0f 29 8c 24 20 01 	movapd %xmm1,0x120(%rsp)
   4d215:	00 00 
   4d217:	48 01 c8             	add    %rcx,%rax
   4d21a:	66 0f 29 8c 24 70 01 	movapd %xmm1,0x170(%rsp)
   4d221:	00 00 
   4d223:	66 0f 29 ac 24 60 01 	movapd %xmm5,0x160(%rsp)
   4d22a:	00 00 
   4d22c:	66 0f 29 ac 24 80 01 	movapd %xmm5,0x180(%rsp)
   4d233:	00 00 
   4d235:	0f 28 04 24          	movaps (%rsp),%xmm0
   4d239:	0f 29 84 24 90 01 00 	movaps %xmm0,0x190(%rsp)
   4d240:	00 
   4d241:	66 0f 28 cf          	movapd %xmm7,%xmm1
   4d245:	66 0f 29 bc 24 a0 01 	movapd %xmm7,0x1a0(%rsp)
   4d24c:	00 00 
   4d24e:	66 41 0f 28 e8       	movapd %xmm8,%xmm5
   4d253:	66 44 0f 29 84 24 b0 	movapd %xmm8,0x1b0(%rsp)
   4d25a:	01 00 00 
   4d25d:	66 0f 29 b4 24 c0 01 	movapd %xmm6,0x1c0(%rsp)
   4d264:	00 00 
   4d266:	66 41 0f 28 fa       	movapd %xmm10,%xmm7
   4d26b:	66 44 0f 29 94 24 d0 	movapd %xmm10,0x1d0(%rsp)
   4d272:	01 00 00 
   4d275:	66 45 0f 28 c3       	movapd %xmm11,%xmm8
   4d27a:	66 44 0f 29 9c 24 e0 	movapd %xmm11,0x1e0(%rsp)
   4d281:	01 00 00 
   4d284:	66 44 0f 29 8c 24 f0 	movapd %xmm9,0x1f0(%rsp)
   4d28b:	01 00 00 
   4d28e:	66 45 0f 28 d5       	movapd %xmm13,%xmm10
   4d293:	66 44 0f 29 ac 24 00 	movapd %xmm13,0x200(%rsp)
   4d29a:	02 00 00 
   4d29d:	66 45 0f 28 de       	movapd %xmm14,%xmm11
   4d2a2:	66 44 0f 29 b4 24 10 	movapd %xmm14,0x210(%rsp)
   4d2a9:	02 00 00 
   4d2ac:	66 44 0f 29 a4 24 20 	movapd %xmm12,0x220(%rsp)
   4d2b3:	02 00 00 
   4d2b6:	66 44 0f 28 ac 24 10 	movapd 0x110(%rsp),%xmm13
   4d2bd:	01 00 00 
   4d2c0:	66 44 0f 29 ac 24 30 	movapd %xmm13,0x230(%rsp)
   4d2c7:	02 00 00 
   4d2ca:	66 44 0f 28 b4 24 00 	movapd 0x100(%rsp),%xmm14
   4d2d1:	01 00 00 
   4d2d4:	66 44 0f 29 b4 24 40 	movapd %xmm14,0x240(%rsp)
   4d2db:	02 00 00 
   4d2de:	66 44 0f 28 bc 24 f0 	movapd 0xf0(%rsp),%xmm15
   4d2e5:	00 00 00 
   4d2e8:	66 44 0f 29 bc 24 50 	movapd %xmm15,0x250(%rsp)
   4d2ef:	02 00 00 
   4d2f2:	0f 28 84 24 e0 00 00 	movaps 0xe0(%rsp),%xmm0
   4d2f9:	00 
   4d2fa:	0f 29 84 24 60 02 00 	movaps %xmm0,0x260(%rsp)
   4d301:	00 
   4d302:	0f 28 84 24 d0 00 00 	movaps 0xd0(%rsp),%xmm0
   4d309:	00 
   4d30a:	0f 29 84 24 70 02 00 	movaps %xmm0,0x270(%rsp)
   4d311:	00 
   4d312:	0f 28 84 24 c0 00 00 	movaps 0xc0(%rsp),%xmm0
   4d319:	00 
   4d31a:	0f 29 84 24 80 02 00 	movaps %xmm0,0x280(%rsp)
   4d321:	00 
   4d322:	0f 28 84 24 b0 00 00 	movaps 0xb0(%rsp),%xmm0
   4d329:	00 
   4d32a:	0f 29 84 24 90 02 00 	movaps %xmm0,0x290(%rsp)
   4d331:	00 
   4d332:	0f 28 84 24 a0 00 00 	movaps 0xa0(%rsp),%xmm0
   4d339:	00 
   4d33a:	0f 29 84 24 a0 02 00 	movaps %xmm0,0x2a0(%rsp)
   4d341:	00 
   4d342:	0f 28 84 24 90 00 00 	movaps 0x90(%rsp),%xmm0
   4d349:	00 
   4d34a:	0f 29 84 24 b0 02 00 	movaps %xmm0,0x2b0(%rsp)
   4d351:	00 
   4d352:	0f 28 84 24 80 00 00 	movaps 0x80(%rsp),%xmm0
   4d359:	00 
   4d35a:	0f 29 84 24 c0 02 00 	movaps %xmm0,0x2c0(%rsp)
   4d361:	00 
   4d362:	0f 28 44 24 70       	movaps 0x70(%rsp),%xmm0
   4d367:	0f 29 84 24 d0 02 00 	movaps %xmm0,0x2d0(%rsp)
   4d36e:	00 
   4d36f:	0f 28 44 24 60       	movaps 0x60(%rsp),%xmm0
   4d374:	0f 29 84 24 e0 02 00 	movaps %xmm0,0x2e0(%rsp)
   4d37b:	00 
   4d37c:	0f 28 44 24 50       	movaps 0x50(%rsp),%xmm0
   4d381:	0f 29 84 24 f0 02 00 	movaps %xmm0,0x2f0(%rsp)
   4d388:	00 
   4d389:	0f 28 44 24 40       	movaps 0x40(%rsp),%xmm0
   4d38e:	0f 29 84 24 00 03 00 	movaps %xmm0,0x300(%rsp)
   4d395:	00 
   4d396:	0f 28 44 24 30       	movaps 0x30(%rsp),%xmm0
   4d39b:	0f 29 84 24 10 03 00 	movaps %xmm0,0x310(%rsp)
   4d3a2:	00 
   4d3a3:	0f 28 44 24 20       	movaps 0x20(%rsp),%xmm0
   4d3a8:	0f 29 84 24 20 03 00 	movaps %xmm0,0x320(%rsp)
   4d3af:	00 
   4d3b0:	0f 28 44 24 10       	movaps 0x10(%rsp),%xmm0
   4d3b5:	0f 29 84 24 30 03 00 	movaps %xmm0,0x330(%rsp)
   4d3bc:	00 
   4d3bd:	66 0f 29 a4 24 50 01 	movapd %xmm4,0x150(%rsp)
   4d3c4:	00 00 
   4d3c6:	66 0f 29 a4 24 40 03 	movapd %xmm4,0x340(%rsp)
   4d3cd:	00 00 
   4d3cf:	66 0f 28 e1          	movapd %xmm1,%xmm4
   4d3d3:	66 0f 29 9c 24 40 01 	movapd %xmm3,0x140(%rsp)
   4d3da:	00 00 
   4d3dc:	66 0f 29 9c 24 50 03 	movapd %xmm3,0x350(%rsp)
   4d3e3:	00 00 
   4d3e5:	0f 28 1c 24          	movaps (%rsp),%xmm3
   4d3e9:	0f 28 84 24 20 01 00 	movaps 0x120(%rsp),%xmm0
   4d3f0:	00 
   4d3f1:	66 0f 29 94 24 30 01 	movapd %xmm2,0x130(%rsp)
   4d3f8:	00 00 
   4d3fa:	66 0f 29 94 24 60 03 	movapd %xmm2,0x360(%rsp)
   4d401:	00 00 
   4d403:	48 81 c7 00 04 00 00 	add    $0x400,%rdi
   4d40a:	48 39 f7             	cmp    %rsi,%rdi
   4d40d:	0f 85 ad f0 ff ff    	jne    4c4c0 <<adaptive_threadpool_thesis::workload::compute_heavy::ComputeHeavyWorkload as adaptive_threadpool_thesis::workload::Workload>::execute+0x200>
   4d413:	48 89 9c 24 10 08 00 	mov    %rbx,0x810(%rsp)
   4d41a:	00 
   4d41b:	48 8d 8c 24 10 08 00 	lea    0x810(%rsp),%rcx
   4d422:	00 
   4d423:	48 81 c4 10 0a 00 00 	add    $0xa10,%rsp
   4d42a:	5b                   	pop    %rbx
   4d42b:	41 5e                	pop    %r14
   4d42d:	41 5f                	pop    %r15
   4d42f:	c3                   	ret
   4d430:	48 8d 0d f1 a0 0a 00 	lea    0xaa0f1(%rip),%rcx        # f7528 <anon.3e1c49db18314bdeb16fa9997ba83c02.1.llvm.921472285622819944>
   4d437:	ff 15 33 e7 0a 00    	call   *0xae733(%rip)        # fbb70 <_DYNAMIC+0x278>
   4d43d:	cc                   	int3
   4d43e:	cc                   	int3
   4d43f:	cc                   	int3
