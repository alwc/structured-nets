PyTorch

structure/ contains code for matrix multiplication and gradient computation for various structured matrix classes, as well as PyTorch layers for them


## MLP

Example command:
```
python mlp/main.py --dataset mnist_noise_1 --result_dir test --lr 1e-3 --epochs 10 MLP model SHL --class-type toeplitz
```
runs a single hidden layer model with a Toeplitz-like matrix of equal dimensions to the dataset input size.

### Flags
- Dataset, training, and optimizer flags are listed with `python mlp/main.py -h`
- `MLP model {name}` specifies the end-to-end model {name} corresponding to a class in mlp/nets.py
- Each model has its own parameters, which can be listed with `python mlp/main.py MLP model {name} -h`
- The class-type flag accepts a name of a structured class (e.g. 'toeplitz' or 'subdiagonal'\_corner) or an abbreviation (e.g. 't' or 'sdc')

### Multiple parameters
main.py supports passing in multiple parameters for certain optimizer hyperparameters, and it will search over all combinations. For example,
` python mlp/main.py ... --lr 1e-3 2e-3 --mom 0.9 0.99 ... `
will search over 4 combinations of parameters.

For general parameters including model params, this feature can be handled with tools such as xargs or GNU Parallel. E.g.
` python mlp/main.py MLP model SHL --class-type ::: t sd ::: -r ::: 1 4 16 `
runs Toeplitz-like and LDR subdiagonal ranks 1,4,16.


## Other tasks

Probably broken right now