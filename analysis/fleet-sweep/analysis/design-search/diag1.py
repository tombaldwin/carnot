import itertools, time
import runner, dsim
from dsim import Design
if __name__=='__main__':
    d=Design(sizes=(1,8),q=2,lam=4,L=90,reps=2,n_p=2,L_p=120,vcal=120)
    for name,kw in [('base',{}),('cv0',dict(cv_window=0.0)),('fastCI',dict(truth_over=dict(ci_hidden_min=0.3,ci_post_min=0.5))),
                    ('cv0+fastCI',dict(cv_window=0.0,truth_over=dict(ci_hidden_min=0.3,ci_post_min=0.5)))]:
        res=runner.run([d],runner.FAM4,300,**kw)
        m=runner.summarise(res[d])
        print(name, {t:(round(m[f'rec_{t}'],2), m[f'picks_{t}']) for t in runner.FAM4}, 'pilotV', [round(x,2) for x in m['pilotV_carnot']])
