import numpy as np, dsim
from dsim import Design
from synth import simulate, make_task_pool
from derive import derive_window
from concurrent.futures import ProcessPoolExecutor
def one(args):
    tname,n,L,seed,over=args
    d=Design(q=2,lam=4)
    tr=dsim.build_truth(d,tname,cv_window=0.0,truth_over=over)
    pool=make_task_pool(tr,seed)
    r,e=simulate(tr,n,seed=seed,window_min=L,task_pool=pool)
    s=derive_window(r,e)['summary']
    return s['attempts'],s['finished'],s['hours'],s['worker_hours']
if __name__=='__main__':
    ex=ProcessPoolExecutor(9)
    for over in [{}, dict(ci_hidden_min=0.3,ci_post_min=0.5)]:
      for tname in ['usl','amdahl','linear','carnot']:
        for L in [90]:
          row=[]
          for n in [1,2,4,8]:
            rs=list(ex.map(one,[(tname,n,L,s,over) for s in range(1,201)]))
            a=np.array(rs); att=a[:,0].sum(); fin=a[:,1].sum(); h=a[:,2].sum(); wh=a[:,3].sum()
            row.append(f"N{n}: att/h {att/h:5.1f} fin/h {fin/h:5.1f} c {fin/att:.2f}")
          print(over!={}, tname,L,' | '.join(row))
