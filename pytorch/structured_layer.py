import torch
from torch.autograd import Variable
import torch.nn as nn
from torch.nn.parameter import Parameter

from torch_krylov import *
from torch_reconstruction import *
import sys
sys.path.insert(0, '../krylov/')
import toeplitz_gpu as toep
import krylov_multiply as kry
import circulant as circ

class StructuredLinear(nn.Module):
    abbrev = {'circulant_sparsity': 'cs', 'tridiagonal_corner': 'tdc', 'tridiagonal_corners': 'tcs', 'low_rank': 'lr', 'unconstrained': 'u',
        'toeplitz_like': 't', 'toeplitz_corner': 'tc', 'toeplitz': 't', 'subdiagonal': 'sd', 'hankel_like': 'h', 'vandermonde_like': 'v', 'circulant': 'c'}

    def name(self):
        return self.__class__.abbrev[self.class_type] + str(self.r)

    def __init__(self, params=None, class_type=None, layer_size=None, init_stddev=0.01, r=1, tie_operators=False, bias=False):
        super(StructuredLinear,self).__init__()
        if params is None:
            assert None not in (class_type, layer_size)
            self.class_type = class_type
            self.layer_size = layer_size
            self.init_stddev = init_stddev
            self.r = r
            self.tie_operators = tie_operators
            self.bias = bias
        else:
            # TODO use defaults if params doesn't have it
            self.class_type = params.class_type
            self.layer_size = params.layer_size
            self.init_stddev = params.init_stddev
            self.r = params.r
            self.tie_operators = params.tie_operators_same_layer
            self.bias = params.bias
            self.params = params
        if self.class_type == 'unconstrained':
            self.W = Parameter(torch.Tensor(self.layer_size, self.layer_size))
            torch.nn.init.normal_(self.W, std=self.init_stddev)
        elif self.class_type == 'circulant':
            self.c = Parameter(torch.Tensor(self.layer_size))
            torch.nn.init.normal_(self.c, std=self.init_stddev)
        elif self.class_type in ['low_rank', 'toeplitz_like', 'vandermonde_like', 'hankel_like',
                                        'circulant_sparsity', 'tridiagonal_corner', 'toeplitz', 'toeplitz_corner', 'subdiagonal']:
            self.G = Parameter(torch.Tensor(self.r, self.layer_size))
            self.H = Parameter(torch.Tensor(self.r, self.layer_size))
            torch.nn.init.normal_(self.G, std=self.init_stddev)
            torch.nn.init.normal_(self.H, std=self.init_stddev)

            if self.class_type == 'low_rank':
                pass
            elif self.class_type == 'toeplitz_corner':
                self.cycle = True
            elif self.class_type == 'toeplitz':
                self.cycle = False
            elif self.class_type == 'subdiagonal':
                self.subd_A = Parameter(torch.ones(self.layer_size))
                if self.tie_operators:
                    self.subd_B = self.subd_A
                else:
                    self.subd_B = Parameter(torch.ones(self.layer_size))
            elif self.class_type == 'tridiagonal_corner':
                self.subd_A = Parameter(torch.ones(self.layer_size-1))
                self.diag_A = Parameter(torch.zeros(self.layer_size))
                self.supd_A = Parameter(torch.zeros(self.layer_size-1))
                if self.tie_operators:
                    self.subd_B = self.subd_A
                    self.diag_B = self.diag_A
                    self.supd_B = self.supd_A
                else:
                    self.subd_B = Parameter(torch.ones(self.layer_size-1))
                    self.diag_B = Parameter(torch.zeros(self.layer_size))
                    self.supd_B = Parameter(torch.zeros(self.layer_size-1))
            else:
                fn_A, fn_B_T = self.set_mult_fns(self.params)
                self.fn_A = fn_A
                self.fn_B_T = fn_B_T
        else:
            # print((f"{self.__class__.__name__} does not support {self.class_type}"))
            # assert 0
            assert 0, f"{self.__class__.__name__} does not support {self.class_type}"

        self.b = None
        if self.bias:
            self.b = Parameter(torch.zeros(self.layer_size))

    # Assumes Stein displacement.
    def set_mult_fns(self, params):
        assert params.disp_type == 'stein'
        if params.class_type in ['toeplitz_like', 'toep_corner', 'toep_nocorn']:
            fn_A = functools.partial(Z_mult_fn, 1)
            fn_B_T = functools.partial(Z_mult_fn, -1)
        # TODO: operators for hankel and vandermonde have not been checked for transpose consistency
        elif params.class_type == 'hankel_like':
            fn_A = functools.partial(Z_transpose_mult_fn, 1)
            fn_B_T = functools.partial(Z_mult_fn, 0)
        elif params.class_type == 'vandermonde_like':
            v = Parameter(torch.Tensor(params.layer_size))
            torch.nn.init.normal_(v,std=params.init_stddev)
            self.v = v
            fn_A = functools.partial(diag_mult_fn, self.v)
            fn_B_T = functools.partial(Z_transpose_mult_fn, 0)
        elif params.class_type == 'circulant_sparsity':
            self.subdiag_f_A = Parameter(torch.Tensor(params.layer_size))
            self.subdiag_f_B = Parameter(torch.Tensor(params.layer_size))
            torch.nn.init.normal_(self.subdiag_f_A,std=params.init_stddev)
            torch.nn.init.normal_(self.subdiag_f_B,std=params.init_stddev)

            fn_A = functools.partial(circ_mult_fn, self.subdiag_f_A)
            fn_B_T = functools.partial(circ_mult_fn, self.subdiag_f_B)

        elif params.class_type == 'tridiagonal_corner':
            self.subdiag_f_A = Parameter(torch.Tensor(params.layer_size))
            self.subdiag_f_B = Parameter(torch.Tensor(params.layer_size))
            self.diag_A = Parameter(torch.Tensor(params.layer_size))
            self.diag_B = Parameter(torch.Tensor(params.layer_size))
            self.supdiag_A = Parameter(torch.Tensor(params.layer_size-1))
            self.supdiag_B = Parameter(torch.Tensor(params.layer_size-1))

            torch.nn.init.normal_(self.subdiag_f_A,std=params.init_stddev)
            torch.nn.init.normal_(self.subdiag_f_B,std=params.init_stddev)
            torch.nn.init.normal_(self.diag_A,std=params.init_stddev)
            torch.nn.init.normal_(self.diag_B,std=params.init_stddev)
            torch.nn.init.normal_(self.supdiag_A,std=params.init_stddev)
            torch.nn.init.normal_(self.supdiag_B,std=params.init_stddev)

            fn_A = functools.partial(tridiag_mult_fn, self.subdiag_f_A, self.diag_A, self.supdiag_A)
            fn_B_T = functools.partial(tridiag_mult_fn, self.subdiag_f_B, self.diag_B, self.supdiag_B)

        else:
            print(('Not supported: ', params.class_type))
            assert 0
        return fn_A, fn_B_T


    def forward(self, x):
        #print('class_type: ', self.class_type)
        if self.class_type == 'unconstrained':
            out = torch.matmul(x, self.W)
        elif self.class_type == 'circulant':
            out = circ.circulant_multiply(self.c, x, self.layer_size)
        elif self.class_type == 'low_rank':
            xH = torch.matmul(x, self.H.t())
            out = torch.matmul(xH, self.G)
        elif self.class_type in ['toeplitz', 'toeplitz_corner']:
            out = toep.toeplitz_mult(self.G, self.H, x, self.cycle)
            # out = toep.toeplitz_mult(self.G, self.G, x, self.cycle)
        elif self.class_type == 'subdiagonal':
            out = kry.subdiag_mult(self.subd_A, self.subd_B, self.G, self.H, x)
            #print('subdiagonal mult slow fast')
            # out = kry.subdiag_mult_slow_fast(self.subd_A, self.subd_B, self.G, self.H, x)
        elif self.class_type == 'tridiagonal_corner':
            out = kry.tridiag_mult_slow(self.subd_A, self.diag_A, self.supd_A, self.subd_B, self.diag_B, self.supd_B, self.G, self.H, x)
        elif self.class_type in ['toeplitz_like', 'vandermonde_like', 'hankel_like',
            'circulant_sparsity']:

            W = recon(self)

            out = torch.matmul(x, W.t())

        if self.b is not None:
            return self.b + out
        else:
            return out

    def loss(self):
        return 0
