import itertools, json, time
import runner, dsim
from dsim import Design
t=time.time()
if __name__=='__main__':
    designs=[]
    for sizes,q,lam,vcal in itertools.product([(1,4),(1,6),(1,8),(2,8),(1,3,8)],[1.5,2,2.5,3,3.5,4.5],[4,8],[0,120]):
        designs.append(Design(sizes=sizes,q=q,lam=lam,L=90,reps=2,n_p=2,L_p=120,vcal=vcal))
    res=runner.run(designs, runner.FAM4, 150)
    rows=[]
    for d in designs:
        m=runner.summarise(res[d])
        rows.append((d.label(), round(dsim.design_cost(d)['total']), *[round(m[f'rec_{t}'],2) for t in runner.FAM4], round(m['rec_min'],2), round(m['tie_usl'],2)))
    rows.sort(key=lambda r:-r[6])
    for r in rows: print(r)
    print(time.time()-t)
    