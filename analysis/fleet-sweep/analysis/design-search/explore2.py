import itertools, time
import runner, dsim
from dsim import Design
if __name__=='__main__':
    t=time.time()
    sweeps=[dict(worker='sonnet',lam=8,q=2,sizes=(1,12),L=60,reps=3),
            dict(worker='haiku',lam=8,q=1.5,sizes=(1,12),L=60,reps=4),
            dict(worker='haiku',lam=8,q=2,sizes=(1,10),L=90,reps=4),
            dict(worker='sonnet',lam=8,q=2,sizes=(1,8),L=60,reps=3)]
    pilots=[dict(n_p=np_,L_p=lp,pw=pw,vcal=vc) for np_,lp,pw,vc in itertools.product([1,2,4],[60,120],[1,2,4],[0,120])]
    designs=[Design(**sw,**pl) for sw in sweeps for pl in pilots]
    res=runner.run(designs, runner.FAM4, 150)
    rows=[]
    for d in designs:
        m=runner.summarise(res[d])
        rows.append((d.label(), (round(m['cost_carnot']) if m['cost_carnot']==m['cost_carnot'] else 'OVER'), *[round(m[f'rec_{t}'],2) for t in runner.FAM4], round(m['rec_min'],2)))
    rows.sort(key=lambda r:-r[-1])
    for r in rows[:40]: print(r)
    print('...')
    for r in rows[-8:]: print(r)
    print(time.time()-t)
