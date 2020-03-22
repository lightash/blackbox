class blackbox():
    """
    A Python module for parallel optimization of expensive black-box functions.
    """
    import numpy as np


    def __init__(self, continue_search=True, widen_search=False):
        self.continue_search = continue_search
        self.widen_search = widen_search


    def get_default_executor():
        """
        Provide a default executor (a context manager
        returning an object with a map method).

        This is the multiprocessing Pool object () for python3.

        The multiprocessing Pool in python2 does not have an __enter__
        and __exit__ method, this function provides a backport of the python3 Pool
        context manager.

        Returns
        -------
        Pool : executor-like object
            An object with context manager (__enter__, __exit__) and map method.
        """
        import sys
        import multiprocessing as mp
        if (sys.version_info > (3, 0)):
            Pool = mp.Pool
            return Pool
        else:
            from contextlib import contextmanager
            from functools import wraps

            @wraps(mp.Pool)
            @contextmanager
            def Pool(*args, **kwargs):
                pool = mp.Pool(*args, **kwargs)
                yield pool
                pool.terminate()
            return Pool


    def search_min(self, f, domain, budget, batch, resfile,
                   rho0=0.5, p=1.0,
                   executor=get_default_executor()):
        """
        Minimize given expensive black-box function and save results into text file.

        Parameters
        ----------
        f : callable
            The objective function to be minimized.
        domain : list of lists
            List of ranges for each parameter.
        budget : int
            Total number of function calls available.
        batch : int
            Number of function calls evaluated simultaneously (in parallel).
        resfile : str
            Text file to save results.
        rho0 : float, optional
            Initial "balls density".
        p : float, optional
            Rate of "balls density" decay (p=1 - linear, p>1 - faster, 0<p<1 - slower).
        executor : callable, optional
            Should have a map method and behave as a context manager.
            Allows the user to use various parallelisation tools
            as dask.distributed or pathos.
        """
        import os
        import scipy.optimize as op
        import datetime

        # continue work
        savefile = f'{resfile[:-3]}npz'
        if os.path.isfile(savefile) and self.continue_search:
            print(f'[blackbox] Optimisation continues from {savefile}.')
            # load previous results
            with np.load(savefile) as npzfile:
                curr_iter = npzfile['curr_iter'] + 1
                domain = npzfile['domain']
                points = npzfile['points']
                if not self.widen_search:
                    budget = npzfile['budget']
        else:
            print(f'[blackbox] No {savefile} to continue, starting new search.')
            curr_iter = 0

        # space size
        d = len(domain)

        # adjusting the budget to the batch size
        if budget % batch != 0:
            budget = budget - budget % batch + batch
            print(f'[blackbox] FYI: budget was adjusted to be {budget}')

        # default global-vs-local assumption (50-50)
        n = budget//2
        if n % batch != 0:
            n = n - n % batch + batch
        m = budget-n

        # n has to be greater than d
        if n <= d:
            print('[blackbox] ERROR: budget is not sufficient')
            return

        # go from normalized values (unit cube) to absolute values (box)
        def cubetobox(x):
            return [domain[i][0]+(domain[i][1]-domain[i][0])*x[i] for i in range(d)]

        def save_csv(results, fmax=1, isArgsort=False):
            results[:, :-1] = list(map(cubetobox, results[:, :-1]))
            results[:, -1] *= fmax
            if isArgsort:
                results = results[results[:, -1].argsort()]
            labels = [' par_' + str(i+1) + (7-len(str(i+1))) * ' ' + ',' for i in range(d)] + [' f_value    ']
            np.savetxt(resfile, results, delimiter=',', fmt=' %+1.4e', header=''.join(labels), comments='')

        def get_str_time():
            return str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        if not continue_search:
            # generating R-sequence
            points = np.zeros((n, d+1))
            points[:, 0:-1] = rseq(n, d)
        elif widen_search:
            points = np.append(points, np.zeros((n, d+1)), axis=0)
            # points = np.append(points[:, -(d+1):], np.zeros((n, d+1)), axis=0)
            n = points.shape[0]
            points[curr_iter*batch:, :-1] = rseq(n-curr_iter*batch, d)

        # initial sampling
        for i in range(curr_iter, n//batch):
            print(f'[blackbox] evaluating batch {i+1}/{(n+m)//batch} (samples {i*batch+1}..{(i+1)*batch}/{n+m}) @ {get_str_time()}...')
            if os.path.isfile('stop'):
                print('[blackbox] stopped')
                return
            with executor() as e:
                points[batch*i:batch*(i+1), -1] = list(e.map(f, list(map(cubetobox, points[batch*i:batch*(i+1), 0:-1]))))
            np.savez_compressed(savefile, domain=domain, budget=budget, batch=batch, points=points, curr_iter=i)
            # saving results into text file
            save_csv(points.copy())

        # normalizing function values
        fmax = max(abs(points[:, -1]))
        points[:, -1] /= fmax

        # volume of d-dimensional ball (r = 1)
        if d % 2 == 0:
            v1 = np.pi**(d/2)/np.math.factorial(d/2)
        else:
            v1 = 2*(4*np.pi)**((d-1)/2)*np.math.factorial((d-1)/2)/np.math.factorial(d)

        # subsequent iterations (current subsequent iteration = i*batch+j)

        for i in range(curr_iter-n//batch, m//batch):
            print(f'[blackbox] evaluating batch {n//batch+i+1}/{(n+m)//batch} (samples {n+i*batch+1}..{n+(i+1)*batch}/{n+m}) @ {get_str_time()}...')
            if os.path.isfile('stop'):
                print('Stopped')
                return

            # sampling next batch of points
            fit = rbf(points)
            points = np.append(points, np.zeros((batch, d+1)), axis=0)

            for j in range(batch):
                r = ((rho0*((m-1.-(i*batch+j))/(m-1.))**p)/(v1*(n+i*batch+j)))**(1./d)
                cons = [{'type': 'ineq', 'fun': lambda x, localk=k: np.linalg.norm(np.subtract(x, points[localk, 0:-1])) - r}
                        for k in range(n+i*batch+j)]
                while True:
                    minfit = op.minimize(fit, np.random.rand(d), method='SLSQP', bounds=[[0., 1.]]*d, constraints=cons)
                    if np.isnan(minfit.x)[0] == False:
                        break
                points[n+i*batch+j, 0:-1] = np.copy(minfit.x)

            with executor() as e:
                points[n+batch*i:n+batch*(i+1), -1] = list(e.map(f, list(map(cubetobox, points[n+batch*i:n+batch*(i+1), 0:-1]))))/fmax

            np.savez_compressed(savefile, domain=domain, budget=budget, batch=batch, points=points, curr_iter=i+n//batch)
            # saving results into text file
            save_csv(points.copy(), fmax)

        # saving final results into text file
        save_csv(points, fmax, isArgsort=True)

        print(f'[blackbox] DONE: see results in {resfile} @ {get_str_time()}')


    @staticmethod
    def rseq(n, d):
        """
        Build R-sequence (http://extremelearning.com.au/unreasonable-effectiveness-of-quasirandom-sequences/).

        Parameters
        ----------
        n : int
            Number of points.
        d : int
            Size of space.

        Returns
        -------
        points : ndarray
            Array of points uniformly placed in d-dimensional unit cube.
        """
        phi = 2
        for i in range(10):
            phi = pow(1+phi, 1./(d+1))

        alpha = np.array([pow(1./phi, i+1) for i in range(d)])

        points = np.array([(0.5 + alpha*(i+1)) % 1 for i in range(n)])

        return points


    @staticmethod
    def rbf(points):
        """
        Build RBF-fit for given points (see Holmstrom, 2008 for details).

        Parameters
        ----------
        points : ndarray
            Array of multi-d points with corresponding values [[x1, x2, .., xd, val], ...].

        Returns
        -------
        fit : callable
            Function that returns the value of the RBF-fit at a given point.
        """
        n = len(points)
        d = len(points[0])-1

        def phi(r):
            return r*r*r

        Phi = [[phi(np.linalg.norm(np.subtract(points[i, 0:-1], points[j, 0:-1]))) for j in range(n)] for i in range(n)]

        P = np.ones((n, d+1))
        P[:, 0:-1] = points[:, 0:-1]

        F = points[:, -1]

        M = np.zeros((n+d+1, n+d+1))
        M[0:n, 0:n] = Phi
        M[0:n, n:n+d+1] = P
        M[n:n+d+1, 0:n] = np.transpose(P)

        v = np.zeros(n+d+1)
        v[0:n] = F

        try:
            sol = np.linalg.solve(M, v)
        except:
            # might help with singular matrices
            print('Singular matrix occurred during RBF-fit construction. RBF-fit might be inaccurate!')
            sol = np.linalg.lstsq(M, v)[0]

        lam, b, a = sol[0:n], sol[n:n+d], sol[n+d]

        def fit(x):
            return sum(lam[i]*phi(np.linalg.norm(np.subtract(x, points[i, 0:-1]))) for i in range(n)) + np.dot(b, x) + a

        return fit
