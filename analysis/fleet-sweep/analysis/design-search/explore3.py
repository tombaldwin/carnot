import itertools, time
import runner, dsim
from dsim import Design
if __name__=='__main__':
    t=time.time()
    sweeps=[dict(worker='sonnet',lam=8,q=2,sizes=(1,12),L=60,reps=3),
            dict(worker='haiku',lam=8,q=1.5,sizes=(1,12),L=60,reps=4),
            dict(worker='haiku',lam=8,q=2,sizes=(1,10),L=90,reps=4),
            dict(worker='sonnet',lam=8,q=2,sizes=(1,8),L=60,reps=3),
            dict(worker='sonnet',lam=6,q=2,sizes=(1,4,12),L=60,reps=2)]
    pilots=[dict(n_p=1,L_p=60,pw=pw,vcal=120) for pw in (2,4,8)]
    designs=[Design(**sw,**pl) for sw in sweeps for pl in pilots]
    for al in (False,True):
        res=runner.run(designs, runner.FAM4, 200, anchor_low=al)
        for d in designs:
            m=runner.summarise(res[d])
            c=m['cost_carnot']
            print('anchorlow' if al else 'plan     ', d.label(), round(c) if c==c else 'OVER', *[round(m[f'rec_{t}'],2) for t in runner.FAM4], 'min',round(m['rec_min'],2))
    print(time.time()-t)
