import sys, time, os, argparse
import yaml
import torch
import glob
import warnings
from utils import *
from SpeakerNet import *
from DatasetLoader import *
import torch.distributed as dist
import torch.multiprocessing as mp
warnings.simplefilter("ignore")

## ===== ===== ===== ===== ===== ===== ===== =====
## Parse arguments
## ===== ===== ===== ===== ===== ===== ===== =====

parser = argparse.ArgumentParser(description = "SpeakerNet")

parser.add_argument('--config',         type=str,   default=None,   help='Config YAML file')

## Data loader
parser.add_argument('--max_frames',     type=int,   default=200,    help='Input length to the network for training')
parser.add_argument('--eval_frames',    type=int,   default=300,    help='Input length to the network for testing 0 uses the whole files')
parser.add_argument('--batch_size',     type=int,   default=200,    help='Batch size, number of speakers per batch')
parser.add_argument('--max_seg_per_spk', type=int,  default=500,    help='Maximum number of utterances per speaker per epoch')
parser.add_argument('--nDataLoaderThread', type=int, default=4,     help='Number of loader threads')
parser.add_argument('--augment',        type=bool,  default=False,  help='Augment input')
parser.add_argument('--seed',           type=int,   default=10,     help='Seed for the random number generator')

## Training details
parser.add_argument('--test_interval',  type=int,   default=1,     help='Test and save every [test_interval] epochs')
parser.add_argument('--max_epoch',      type=int,   default=100,    help='Maximum number of epochs')
parser.add_argument('--trainfunc',      type=str,   default="",     help='Loss function')

## Optimizer
parser.add_argument('--optimizer',      type=str,   default="adam", help='sgd or adam')
parser.add_argument('--scheduler',      type=str,   default="steplr", help='Learning rate scheduler')
parser.add_argument('--lr',             type=float, default=0.001,  help='Learning rate')
parser.add_argument("--lr_decay",       type=float, default=0.95,   help='Learning rate decay every [lr_step] epochs')
parser.add_argument('--weight_decay',   type=float, default=2e-5,      help='Weight decay in the optimizer')
parser.add_argument('--lr_step',        type=int,   default=2,      help='Step for learning rate decay')
parser.add_argument('--step_size_up',   type=int,   default=20000,   help='step_size_up of CyclicLR')
parser.add_argument('--step_size_down',   type=int, default=20000,   help='step_size_down of CyclicLR')
parser.add_argument('--cyclic_mode',    type=str,   default='triangular2', help='policy of CyclicLR')

## Loss functions
parser.add_argument("--hard_prob",      type=float, default=0.5,    help='Hard negative mining probability, otherwise random, only for some loss functions')
parser.add_argument("--hard_rank",      type=int,   default=10,     help='Hard negative mining rank in the batch, only for some loss functions')
parser.add_argument('--margin',         type=float, default=0.2,    help='Loss margin, only for some loss functions')
parser.add_argument('--scale',          type=float, default=30,     help='Loss scale, only for some loss functions')
parser.add_argument('--nPerSpeaker',    type=int,   default=1,      help='Number of utterances per speaker per batch, only for metric learning based losses')
parser.add_argument('--nClasses',       type=int,   default=750,   help='Number of speakers in the softmax layer, only for softmax-based losses')

## Evaluation parameters
parser.add_argument('--dcf_p_target',   type=float, default=0.05,   help='A priori probability of the specified target speaker')
parser.add_argument('--dcf_c_miss',     type=float, default=1,      help='Cost of a missed detection')
parser.add_argument('--dcf_c_fa',       type=float, default=1,      help='Cost of a spurious detection')

## Load and save
parser.add_argument('--initial_model',  type=str,   default="",     help='Initial model weights')
parser.add_argument('--save_path',      type=str,   default="exps/exp1", help='Path for model and logs')

## Training and test data
parser.add_argument('--train_list',     type=str,   default="data/MSV_CommonVoice_data/metadata/all_new_metadata2.txt",  help='Train list')
parser.add_argument('--test_list',      type=str,   default="data/Test/veri_test2.txt",   help='Evaluation list')
parser.add_argument('--train_path',     type=str,   default="/home2/vietvq/UNDERFITT/data/MSV_CommonVoice_data/", help='Absolute path to the train set')
parser.add_argument('--test_path',      type=str,   default="data/Test/wav", help='Absolute path to the test set')
parser.add_argument('--musan_path',     type=str,   default="data/musan_augment/", help='Absolute path to the test set')
parser.add_argument('--rir_path',       type=str,   default="data/rirs_noises/", help='Absolute path to the test set')
parser.add_argument('--output_path',    type=str,   default='output/submission/t1',     help='Output path for storing testing results')
parser.add_argument('--output_filename_txt',    type=str,   default='output.txt',     help='Output filename for storing testing results txt')
parser.add_argument('--output_filename_pk',    type=str,   default='output.pk',     help='Output filename for storing testing embd results')



## Model definition
parser.add_argument('--n_mels',         type=int,   default=80,     help='Number of mel filterbanks')
parser.add_argument('--log_input',      type=bool,  default=False,  help='Log input features')
parser.add_argument('--model',          type=str,   default="",     help='Name of model definition')
parser.add_argument('--encoder_type',   type=str,   default="SAP",  help='Type of encoder')
parser.add_argument('--nOut',           type=int,   default=512,    help='Embedding size in the last FC layer')
parser.add_argument('--sinc_stride',    type=int,   default=10,     help='Stride size of the first analytic filterbank layer of RawNet3')
parser.add_argument('--C',              type=int,   default=1024,   help='Channel size for the speaker encoder (ECAPA_TDNN)')

## For test only
parser.add_argument('--train',           dest='train', action='store_true', help='Train only')
parser.add_argument('--eval',           dest='eval', action='store_true', help='Eval only')
parser.add_argument('--save_embedding',           dest='save_embedding', action='store_true', help='extract embedding')
parser.add_argument('--save_embedding_train',      dest='save_embedding_train', action='store_true', help='extract embedding')
parser.add_argument('--test',           dest='test', action='store_true', help='Test only')
parser.add_argument('--freeze',         dest='freeze', action='store_true')
parser.add_argument('--unfreeze_embedding', dest='unfreeze_embedding', action='store_true')
parser.add_argument('--asnorm',           dest='asnorm', action='store_true', help='asnorm score')

## Distributed and mixed precision training
parser.add_argument('--port',           type=str,   default="8888", help='Port for distributed training, input as text')
parser.add_argument('--distributed',    dest='distributed', action='store_true', help='Enable distributed training')
parser.add_argument('--mixedprec',      dest='mixedprec',   action='store_true', help='Enable mixed precision training')

args = parser.parse_args()

## Parse YAML
def find_option_type(key, parser):
    for opt in parser._get_optional_actions():
        if ('--' + key) in opt.option_strings:
           return opt.type
    raise ValueError

if args.config is not None:
    with open(args.config, "r") as f:
        yml_config = yaml.load(f, Loader=yaml.FullLoader)
    for k, v in yml_config.items():
        if k in args.__dict__:
            typ = find_option_type(k, parser)
            args.__dict__[k] = typ(v)
        else:
            sys.stderr.write("Ignored unknown parameter {} in yaml.\n".format(k))


## ===== ===== ===== ===== ===== ===== ===== =====
## Trainer script
## ===== ===== ===== ===== ===== ===== ===== =====

def main_worker(gpu, ngpus_per_node, args):
    print("Start training process\n")
    print("nClasses: ", args.nClasses)

    args.gpu = gpu
    torch.backends.cudnn.benchmark = True

    ## Load models
    s = SpeakerNet(**vars(args))

    # assign gpu to model
    if args.distributed:
        os.environ['MASTER_ADDR']='localhost'
        os.environ['MASTER_PORT']=args.port

        dist.init_process_group(backend='nccl', world_size=ngpus_per_node, rank=args.gpu)

        torch.cuda.set_device(args.gpu)
        s.cuda(args.gpu)

        s = torch.nn.parallel.DistributedDataParallel(s, device_ids=[args.gpu], find_unused_parameters=True)

        print('Loaded the model on GPU {:d}'.format(args.gpu))

    else:
        s = WrappedModel(s).cuda(args.gpu)

    it = 1
    eers = [100]

    if args.gpu == 0:
        ## Write args to scorefile
        scorefile   = open(args.result_save_path+"/scores.txt", "a+")

    if args.train:
    ## Initialise trainer and data loader
        train_dataset = train_dataset_loader(**vars(args))

        train_sampler = train_dataset_sampler(train_dataset, **vars(args))

        train_loader = torch.utils.data.DataLoader(
            train_dataset,
            batch_size=args.batch_size,
            num_workers=args.nDataLoaderThread,
            sampler=train_sampler,
            # shuffle=True,
            pin_memory=True,
            worker_init_fn=worker_init_fn,
            drop_last=True,
        )

    trainer = ModelTrainer(s, **vars(args))

    if args.freeze:
        model = trainer.__model__.module.__S__

        if args.unfreeze_embedding:
            for param in model.parameters():
                param.requires_grad = False

                model.fc6.weight.requires_grad = True
                model.fc6.bias.requires_grad = True
                model.bn6.weight.requires_grad = True
                model.bn6.bias.requires_grad = True
            
            plist = [
                {'params': model.fc6.parameters(), 'lr': 5e-6},
                {'params': model.bn6.parameters(), 'lr': 5e-6},
                {'params': trainer.__model__.module.__L__.parameters(), 'lr': 0.001}
            ]
        else:
            plist = [
                {'params': trainer.__model__.module.__L__.parameters(), 'lr': 0.001}
            ]

        Optimizer = importlib.import_module("optimizer." + args.optimizer).__getattribute__("Optimizer")
        trainer.__optimizer__ = Optimizer(plist, **vars(args))

        Scheduler = importlib.import_module("scheduler." + args.scheduler).__getattribute__("Scheduler")
        del args.optimizer
        trainer.__scheduler__, trainer.lr_step = Scheduler(trainer.__optimizer__, **vars(args))

    ## Load model weights
    modelfiles = glob.glob('%s/model0*.model'%args.model_save_path)
    modelfiles.sort()

    if(args.initial_model != ""):
        trainer.loadParameters(args.initial_model)
        print("Model {} loaded!".format(args.initial_model))
    elif len(modelfiles) >= 1:
        trainer.loadParameters(modelfiles[-1])
        print("Model {} loaded from previous state!".format(modelfiles[-1]))
        it = int(os.path.splitext(os.path.basename(modelfiles[-1]))[0][5:]) + 1

    for ii in range(1, it):
        trainer.__scheduler__.step()

    ## Evaluation code - must run on single GPU
    if args.eval == True:

        pytorch_total_params = sum(p.numel() for p in s.module.__S__.parameters())

        print('Total parameters: ',pytorch_total_params)
        print('Test list',args.test_list)
        
        sc, lab = trainer.eval_network(**vars(args))

        if args.gpu == 0:

            result = tuneThresholdfromScore(sc, lab, [1, 0.1])

            print(time.strftime("%Y-%m-%d %H:%M:%S"), "EER {:2.4f}".format(result[1]), '\n')
            # print the chosen threshold
            print('Chosen threshold', result[4])

        return

    if args.save_embedding == True:
        pytorch_total_params = sum(p.numel() for p in s.module.__S__.parameters())

        print('Total parameters: ',pytorch_total_params)        
        trainer.save_embeddings(**vars(args))

        return
    
    if args.save_embedding_train == True:
        pytorch_total_params = sum(p.numel() for p in s.module.__S__.parameters())

        print('Total parameters: ',pytorch_total_params)        
        trainer.save_embbeddings_2(**vars(args))

        return

    # Test time
    if args.test == True:
        print('Test list', args.test_list)
        if args.asnorm:
            trainer.save_score_as_norm(**vars(args))
        else:
            trainer.save_score_no_norm(**vars(args))

        return

    ## Core training script
    for it in range(it,args.max_epoch+1):
        print(time.strftime("%Y-%m-%d %H:%M:%S"), "Epoch {:d}".format(it))
        train_sampler.set_epoch(it)

        clr = [x['lr'] for x in trainer.__optimizer__.param_groups]

        loss, train_acc = trainer.train_network(train_loader, verbose=(args.gpu == 0)) #Ttrain_acc is acc

        if args.gpu == 0:
            print(time.strftime("%Y-%m-%d %H:%M:%S"), "Epoch {:d}, TAcc: {:.2f}, TLOSS {:f}, LR {:f}".format(it, train_acc, loss, max(clr)))
            scorefile.write("Epoch {:d}, TLOSS {:f}, TAcc {:.2f}, LR {:f} \n".format(it, loss, train_acc, max(clr)))

        if it % args.test_interval == 0:

            # sc, lab, _ = trainer.evaluateFromList(**vars(args))
            sc, lab = trainer.eval_network(**vars(args)) # score and label

            if args.gpu == 0:

                result = tuneThresholdfromScore(sc, lab, [1, 0.1])
                

                # fnrs, fprs, thresholds = ComputeErrorRates(sc, lab)
                # mindcf, threshold = ComputeMinDcf(fnrs, fprs, thresholds, args.dcf_p_target, args.dcf_c_miss, args.dcf_c_fa)

                eers.append(result[1])
              
                print(time.strftime("%Y-%m-%d %H:%M:%S"), "Epoch {:d}, VEER {:2.4f}".format(it, result[1]), '\n')
                scorefile.write("Epoch {:d}, VEER {:2.4f}\n".format(it, result[1]))

                trainer.saveParameters(args.model_save_path+"/model%09d.model"%it)

                scorefile.flush()

    if args.gpu == 0:
        scorefile.close()


## ===== ===== ===== ===== ===== ===== ===== =====
## Main function
## ===== ===== ===== ===== ===== ===== ===== =====


def main():
    args.model_save_path     = args.save_path+"/model"
    args.result_save_path    = args.save_path+"/result"
    args.feat_save_path      = ""

    os.makedirs(args.model_save_path, exist_ok=True)
    os.makedirs(args.result_save_path, exist_ok=True)

    n_gpus = torch.cuda.device_count()

    print('Python Version:', sys.version)
    print('PyTorch Version:', torch.__version__)
    print('Number of GPUs:', torch.cuda.device_count())
    print('Save path:',args.save_path)

    if args.distributed:
        mp.spawn(main_worker, nprocs=n_gpus, args=(n_gpus, args))
    else:
        main_worker(0, None, args)


if __name__ == '__main__':
    main()