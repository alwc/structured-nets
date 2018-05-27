import numpy as np
import torch
from torch.autograd import Variable

from complex_utils import complex_mult


class KT_Toeplitz():
    """Multiply Krylov(A, v)^T @ u when A is zero except on the subdiagonal.
    """

    def __init__(self, n, f=0, batch_size=1, rank=1):
        self.n = n
        self.batch_size = batch_size
        self.rank = rank

        self.eta = None
        if f != 0:
            mod = np.power(np.abs(f), np.arange(n)/n)
            if f > 0:
                arg = np.ones(n)
            else:
                arg = np.fft.fft(np.eye(1,2*n,2*n-1))[0,:n]
            # self.eta = mod * arg
            self.eta = Variable(torch.Tensor((mod * arg).astype('complex64').view('float32')).view(-1, 2), requires_grad=False).cuda()
            self.ieta = Variable(torch.Tensor((1/(mod * arg)).astype('complex64').view('float32')).view(-1, 2), requires_grad=False).cuda()


    def __call__(self, v, u):
        """
        Multiply Krylov(Z_f, v)^T @ u
        v: (rank, n)
        u: (batch, n)
        out: (batch, rank, n)
        """
        n, batch_size, rank = self.n, self.batch_size, self.rank

        if self.eta is not None: # cycle version
            u_ = torch.ifft(self.ieta * u[..., np.newaxis], 1)
            v_ = torch.fft(self.eta * v[..., np.newaxis], 1)
            uv_ = complex_mult(u_[:, np.newaxis], v_[np.newaxis])
            uv = torch.fft(uv_, 1)
            # We only need the real part of complex_mult(self.eta, uv)
            return self.eta[..., 0] * uv[..., 0] - self.eta[..., 1] * uv[..., 1]
        else:
            reverse_index = torch.arange(n-1, -1, -1, dtype=torch.long, device=u.device)
            u_ = torch.rfft(torch.cat((u[...,reverse_index], torch.zeros_like(u)), dim=-1), 1)
            v_ = torch.rfft(torch.cat((v, torch.zeros_like(v)), dim=-1), 1)
            uv_ = complex_mult(u_[:, np.newaxis], v_[np.newaxis])
            return torch.irfft(uv_, 1, signal_sizes=(2 * n, ))[..., reverse_index]
        return ans


class K_Toeplitz():
    """Multiply Krylov(A, v) @ w when A is zero except on the subdiagonal.
    """

    def __init__(self, n, f, batch_size=1, rank=1):
        self.n = n
        self.batch_size = batch_size
        self.rank = rank

        self.eta = None
        if f != 0:
            mod = np.power(np.abs(f), np.arange(n)/n)
            if f > 0:
                arg = np.ones(n)
            else:
                arg = np.fft.fft(np.eye(1,2*n,2*n-1))[0,:n]
            # self.eta = mod * arg
            self.eta = Variable(torch.Tensor((mod * arg).astype('complex64').view('float32')).view(-1, 2), requires_grad=False).cuda()
            self.ieta = Variable(torch.Tensor((1/(mod * arg)).astype('complex64').view('float32')).view(-1, 2), requires_grad=False).cuda()

    def __call__(self, v, w):
        """
        v: (rank, n)
        w: (batch_size, rank, n)
        out: (batch_size, n)
        """
        n, batch_size, rank = self.n, self.batch_size, self.rank
        if self.eta is not None:
            w_ = torch.fft(self.eta * w[..., np.newaxis], 1)
            v_ = torch.fft(self.eta * v[..., np.newaxis], 1)
            wv_sum_ = complex_mult(w_, v_).sum(dim=1)
            wv_sum = torch.ifft(wv_sum_, 1)
            # We only need the real part of complex_mult(self.ieta, wv_sum)
            ans = self.ieta[..., 0] * wv_sum[..., 0] - self.ieta[..., 1] - wv_sum[..., 1]
        else:
            w_ = torch.rfft(torch.cat((w, torch.zeros_like(w)), dim=-1), 1)
            v_ = torch.rfft(torch.cat((v, torch.zeros_like(v)), dim=-1), 1)
            wv_sum_ = complex_mult(w_, v_).sum(dim=1)
            ans = torch.irfft(wv_sum_, 1, signal_sizes=(2 * n, ))[..., :n]
        return ans


def toeplitz_mult(G, H, x, cycle=True):
    rank, n = G.shape
    batch_size = x.shape[0]
    f = (1,-1) if cycle else (0,0)
    # f = (1,-1) if cycle else (1,1)
    transpose_out = KT_Toeplitz(n, f[1], batch_size, rank)(H, x)
    krylov_out = K_Toeplitz(n, f[0], batch_size, rank)(G, transpose_out)
    scaled = krylov_out if cycle else krylov_out
    return scaled

##### AD mult

def multiply_by_autodiff(v, w, f=1):
    """Multiply \sum_i Krylov(A, v_i) @ w_i when A is zero except on the subdiagonal, using Pytorch's autodiff.
    Parameters:
        subdiag: Tensor of shape (n - 1, )
        v: Tensor of shape (rank, n)
        w: Tensor of shape (batch_size, rank, n)
    Returns:
        product: Tensor of shape (batch_size, n)
    """
    batch_size, rank, n = w.shape
    rank_, n_ = v.shape
    assert n == n_, 'w and v must have the same last dimension'
    assert rank == rank_, 'w and v must have the same rank'

    # u = Variable(torch.zeros((batch_size, n)).cuda(), requires_grad=True)
    u = Variable(torch.cuda.FloatTensor(batch_size, n).fill_(0.0), requires_grad=True)
    prod = KT_Toeplitz(n,f,batch_size,rank)(v, u)
    result, = torch.autograd.grad(prod, u, grad_outputs=w, retain_graph=True)
    return result



##### Slow mult

def krylov_construct(f, v, m=None):
    """input: v - Variable"""
    n = v.shape[0]
    if m is None:
        m = n

    cols = [v]
    for _ in range(m-1):
        v = torch.cat((f*v[[-1]], v[:-1]))
        cols.append(v)
    return torch.stack(cols, dim=-1)

def toeplitz_mult_slow(G, H, x, cycle=True):
    assert G.shape == H.shape
    rank, n = G.shape
    f = (1,-1) if cycle else (0,0)
    krylovs = [(krylov_construct(f[0], G[i], n), krylov_construct(f[1], H[i], n).t()) for i in range(rank)]
    prods = [torch.matmul(K[0] , torch.matmul(K[1] , x.t())) for K in krylovs]
    return sum(prods).t()

def krylov_construct_toeplitz(v, f=0.0):
    """Fast construction using indices, so it's vectorized.
    Batched wrt rank of v.
    v: (rank x n) tensor
    f: real number
    """
    rank, n  = v.shape
    a = torch.arange(n, dtype=torch.long, device=v.device)
    b = -a
    indices = a[:, np.newaxis] + b[np.newaxis]
    # Pytorch's advanced indexing (as of 0.4.0) is wrong for negative indices when combined with basic indexing.
    # So we have to make the indices positive.
    # K = v[:, indices]
    K = v[:, indices % n]
    K[:, indices < 0] *= f
    return K

def toeplitz_mult_slow_fast(G, H, x, cycle=True):
    assert G.shape == H.shape
    rank, n = G.shape
    f_G, f_H = (1, -1) if cycle else (0, 0)
    K_G, K_H = krylov_construct_toeplitz(G, f_G), krylov_construct_toeplitz(H, f_H)
    temp = (K_H.transpose(1, 2) @ x.t())
    # result = K_G @ temp3
    # For some reason K_G @ temp3 gives less accurate results than this
    result = torch.stack([K_G_ @ temp_ for K_G_, temp_ in zip(K_G, temp)])
    return result.sum(dim=0).t()

if __name__ == '__main__':
    v = Variable(torch.Tensor([[0,1,0,-1],[0,1,2,3]])).cuda()
    u = Variable(torch.Tensor([[1,1,1,1],[0,1,2,3]])).cuda()

    w = KT_Toeplitz(4, -1, 2, 2)(v, u)
    # output:
    # [[[ 0 2  2 0]
    #   [ 6 0 -4 -6]]

    #  [[ -2 2 4  2]
    #   [ 14 8 0 -8]]]

    toeplitz_mult(v, v, u)
    toeplitz_mult_slow(v, v, u)
    # output:
    # array([[-16., -20.,  -4.,  16.],
    #        [ 16.,  -8.,  12.,  64.]])

    toeplitz_mult(v, v, u, cycle=False)
    toeplitz_mult_slow(v, v, u, cycle=False)
    # output:
    # array([[ 0.,  6., 16., 26.],
    #        [ 0., 12., 38., 66.]])

    m = 10
    n = 1<<m
    batch_size = 50
    rank = 16
    u = torch.rand((batch_size, n), requires_grad=True, device="cuda")
    v = torch.rand((rank, n), requires_grad=True, device="cuda")
    result = toeplitz_mult(v, v, u, cycle=True)
    result_slow = toeplitz_mult_slow(v, v, u, cycle=True)
    result_slow_fast = toeplitz_mult_slow_fast(v, v, u, cycle=True)
    print(np.allclose(result.detach().cpu().numpy(), result_slow.detach().cpu().numpy()))
    print(torch.max(torch.abs(result - result_slow)).item())
    print(torch.mean(torch.abs(result - result_slow)).item())
    print(np.allclose(result.detach().cpu().numpy(), result_slow_fast.detach().cpu().numpy()))
    print(torch.max(torch.abs(result - result_slow_fast)).item())
    print(torch.mean(torch.abs(result - result_slow_fast)).item())


def mem_test():
    for _ in range(10000):
        a = Variable(torch.cuda.FloatTensor((2,4096)), requires_grad=True)
        b = Variable(torch.cuda.FloatTensor((2,4096)), requires_grad=True)
        c = toeplitz_mult(a,a,b)
        g, = torch.autograd.grad(torch.sum(c), a, retain_graph=True)
