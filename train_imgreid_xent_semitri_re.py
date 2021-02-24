from __future__ import print_function
from __future__ import division

import os
import sys
import time
import datetime
import argparse
import os.path as osp
import numpy as np
import re

import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
from torch.utils.data import DataLoader
from torch.optim import lr_scheduler

import data_manager
from dataset_loader import ImageDataset
import transforms as T
import models
from losses import CrossEntropyLabelSmooth, SemiHardTripLoss, DeepSupervision
from utils.iotools import save_checkpoint
from utils.avgmeter import AverageMeter
from utils.logger import Logger
from utils.torchtools import count_num_param
from eval_metrics import evaluate
from samplers import RandomIdentitySampler
from optimizers import init_optim


parser = argparse.ArgumentParser(description='Train image model with cross entropy loss and semi-hard triplet loss')
# Datasets
# parser.add_argument('--root', type=str, default='/home/fan047/MyFile/DataSet/ReID_data/',	#data
#                     help="root path to data directory")
parser.add_argument('--root', type=str, default='/OSM/CBR/D61_RCV/students/fan047/myImplementation/ReID_data', 
                   help="root path to data directory")
parser.add_argument('--bracewell', type=bool, default=False, help="write True to use bracewell")
parser.add_argument('--focused-parts', type=int, default=4, help="number of focused parts")
parser.add_argument('--pretrained', action='store_true', help="whether use pretrained model")
parser.add_argument('--factor-of-scale-factors', type=float, default=1, help="factor of scale factors")
parser.add_argument('--drop-rate', type=float, default=0, help="dropout rate")
parser.add_argument('--multi-query', action='store_true',
                    help="whether to test multi query for market1501")
parser.add_argument('--trans-learn', action='store_true',
                    help="whether to use transform learning")
parser.add_argument('--fine-tune', action='store_true')
parser.add_argument('--mode', type=str, default='max',
                    help="using 'max' or 'avg' in testing multi query")

parser.add_argument('-d', '--dataset', type=str, default='market1501',
                    choices=data_manager.get_names())
parser.add_argument('-j', '--workers', default=4, type=int,
                    help="number of data loading workers (default: 4)")
parser.add_argument('--height', type=int, default=256,
                    help="height of an image (default: 256)")
parser.add_argument('--width', type=int, default=128,
                    help="width of an image (default: 128)")
parser.add_argument('--split-id', type=int, default=0,
                    help="split index")
parser.add_argument('--use-lmdb', action='store_true',
                    help="whether to use lmdb dataset")
# CUHK03-specific setting
parser.add_argument('--cuhk03-labeled', action='store_true',
                    help="whether to use labeled images, if false, detected images are used (default: False)")
parser.add_argument('--cuhk03-classic-split', action='store_true',
                    help="whether to use classic split by Li et al. CVPR'14 (default: False)")
parser.add_argument('--use-metric-cuhk03', action='store_true',
                    help="whether to use cuhk03-metric (default: False)")
# Optimization options
parser.add_argument('--optim', type=str, default='adam',
                    help="optimization algorithm (see optimizers.py)")
parser.add_argument('--max-epoch', default=300, type=int,
                    help="maximum epochs to run")
parser.add_argument('--start-epoch', default=0, type=int,
                    help="manual epoch number (useful on restarts)")
parser.add_argument('--train-batch', default=8, type=int,
                    help="train batch size")
parser.add_argument('--test-batch', default=32, type=int,
                    help="test batch size")
parser.add_argument('--lr', '--learning-rate', default=0.0003, type=float,
                    help="initial learning rate")
parser.add_argument('--stepsize', default=[100, 200], nargs='+', type=int,
                    help="stepsize to decay learning rate")
parser.add_argument('--gamma', default=0.1, type=float,
                    help="learning rate decay")
parser.add_argument('--weight-decay', default=5e-04, type=float,
                    help="weight decay (default: 5e-04)")
parser.add_argument('--margin', type=float, default=0.3,
                    help="margin for triplet loss")
parser.add_argument('--p',      type = float, default = 2,
                    help="p for triplet loss")
parser.add_argument('--num_trip',      type = int, default = 5,
                    help="top num of hard trip for triplet loss")
parser.add_argument('--num-instances', type=int, default=4,
                    help="number of instances per identity")
parser.add_argument('--htri-only', action='store_true', default=False,
                    help="if this is True, only htri loss is used in training")
# Architecture
parser.add_argument('-a', '--arch', type=str, default='bilinear_baseline', choices=models.get_names())
# Miscs
parser.add_argument('--print-freq', type=int, default=10,
                    help="print frequency")
parser.add_argument('--seed', type=int, default=1,
                    help="manual seed")
parser.add_argument('--resume', type=str, default='', metavar='PATH')
parser.add_argument('--evaluate', action='store_true',
                    help="evaluation only")
parser.add_argument('--eval-step', type=int, default=10,
                    help="run evaluation for every N epochs (set to -1 to test after training)")
parser.add_argument('--start-eval', type=int, default=0,
                    help="start to evaluate after specific epoch")
parser.add_argument('--save-dir', type=str, default='log')
parser.add_argument('--use-cpu', action='store_true',
                    help="use cpu")
parser.add_argument('--gpu-devices', default='0', type=str,
                    help='gpu device ids for CUDA_VISIBLE_DEVICES')

parser.add_argument('--feat-dim', type=int, default=512,
                    help="dim of feature (default: 512)")
args = parser.parse_args()


def main():
    if args.bracewell is True:
        args.root = '/flush2/roy030/JIM/data'
        args.save_dir = osp.join('/flush2/roy030/JIM', args.save_dir)
    else:
        pass
    torch.manual_seed(args.seed)
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu_devices
    use_gpu = torch.cuda.is_available()
    if args.use_cpu: use_gpu = False

    if (args.dataset != 'market1501') and args.multi_query == True:
        args.multi_query = False

    # args.arch = 'inceptionv4'
    # args.arch = 'man_share'
    # args.height = 256
    # args.width = 128

    # use_gpu = False
    # args.resume = 'final_log/M_V1/best_model_0.pth.tar'
    # args.fine_tune = True
    # args.pretrained = True
    # args.evaluate = True
    # args.multi_query = True
    # args.dataset = 'msmt17'
    # args.trans_learn = True



    if not args.evaluate:
        sys.stdout = Logger(osp.join(args.save_dir, 'log_train.txt'))
    else:
        sys.stdout = Logger(osp.join(args.save_dir, 'log_test.txt'))
    print("==========\nArgs:{}\n==========".format(args))

    if use_gpu:
        print("Currently using GPU {}".format(args.gpu_devices))
        cudnn.benchmark = True
        torch.cuda.manual_seed_all(args.seed)
    else:
        print("Currently using CPU (GPU is highly recommended)")

    print("Initializing dataset {}".format(args.dataset))
    dataset = data_manager.init_imgreid_dataset(
        root=args.root, name=args.dataset, split_id=args.split_id,
        cuhk03_labeled=args.cuhk03_labeled, cuhk03_classic_split=args.cuhk03_classic_split,
        multi_query=args.multi_query,
        use_lmdb=args.use_lmdb,
    )

    transform_train = T.Compose([
        T.Random2DTranslation(args.height, args.width),
        T.RandomHorizontalFlip(),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        T.RandomErasing(),
    ])

    transform_test = T.Compose([
        T.Resize((args.height, args.width)),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    pin_memory = True if use_gpu else False

    trainloader = DataLoader(
        ImageDataset(dataset.train, transform=transform_train,
                     use_lmdb=args.use_lmdb, lmdb_path=dataset.train_lmdb_path),
        sampler=RandomIdentitySampler(dataset.train, num_instances=args.num_instances),
        batch_size=args.train_batch, num_workers=args.workers,
        pin_memory=pin_memory, drop_last=True,
    )

    queryloader = DataLoader(
        ImageDataset(dataset.query, transform=transform_test,
                     use_lmdb=args.use_lmdb, lmdb_path=dataset.train_lmdb_path),
        batch_size=args.test_batch, shuffle=False, num_workers=args.workers,
        pin_memory=pin_memory, drop_last=False,
    )

    galleryloader = DataLoader(
        ImageDataset(dataset.gallery, transform=transform_test,
                     use_lmdb=args.use_lmdb, lmdb_path=dataset.train_lmdb_path),
        batch_size=args.test_batch, shuffle=False, num_workers=args.workers,
        pin_memory=pin_memory, drop_last=False,
    )

    print("Initializing model: {}".format(args.arch))
    kwargs = {"height": args.height, "width": args.width, "feat_dim": args.feat_dim, "use_gpu": use_gpu,
              "num_focused_parts" : args.focused_parts,
              "factor_of_scale_factors": args.factor_of_scale_factors,
              "drop_rate": args.drop_rate}

    if args.pretrained and args.resume:
        checkpoint = torch.load(args.resume)
        pretrained_dict = checkpoint['state_dict']
        kwargs["pretrained_dict"] = pretrained_dict
        print("Loading pretrained model")
    else:
        pass


    model = models.init_model(name=args.arch, num_classes=dataset.num_train_pids, loss={'xent', 'htri'}, **kwargs)
    print("Model size: {:.3f} M".format(count_num_param(model)))

    criterion_xent = CrossEntropyLabelSmooth(num_classes=dataset.num_train_pids, use_gpu=use_gpu)
    criterion_htri = SemiHardTripLoss(margin=args.margin,p=args.p, num_trip=args.num_trip, use_gpu=use_gpu)
    
    if args.resume and not args.fine_tune and not args.pretrained:
        print("Loading checkpoint from '{}'".format(args.resume))
        checkpoint = torch.load(args.resume)
        print("==========\nOld Args:{}\n==========".format(checkpoint['args']))
        # model.load_state_dict(checkpoint['state_dict'])
        trained_dict = checkpoint['state_dict']
        if args.trans_learn:
            pattern = re.compile('classifier_global|classifier_local')
            for key in list(trained_dict.keys()):
                res = pattern.match(key)
                if res:
                    del trained_dict[key]
                else:
                    pass
            model_dick = model.state_dict()
            model_dick.update(trained_dict)
            model.load_state_dict(model_dick)
            if use_gpu:
                model = nn.DataParallel(model).cuda()
            optimizer = init_optim(args.optim, model.parameters(), args.lr, args.weight_decay)
            scheduler = lr_scheduler.MultiStepLR(optimizer, milestones=args.stepsize, gamma=args.gamma)
            start_epoch = args.start_epoch

        # elif args.pretrained:
        #
        #     model_dick = model.state_dict()
        #
        #     print(1)

        else:
            start_epoch = checkpoint['epoch']+1
            model.load_state_dict(trained_dict)
            if use_gpu:
                model = nn.DataParallel(model).cuda()
            optimizer = init_optim(args.optim, model.parameters(), args.lr, args.weight_decay)
            optimizer.load_state_dict(checkpoint['optimizer'])
            scheduler = lr_scheduler.MultiStepLR(optimizer, milestones=args.stepsize, gamma=args.gamma,
                                                 last_epoch=checkpoint['epoch'])

    elif args.resume and args.fine_tune:
        print("Loading checkpoint from '{}'".format(args.resume))
        checkpoint = torch.load(args.resume)
        print("==========\nOld Args:{}\n==========".format(checkpoint['args']))
        # model.load_state_dict(checkpoint['state_dict'])
        trained_dict = checkpoint['state_dict']
        model.load_state_dict(trained_dict)
        if use_gpu:
            model = nn.DataParallel(model).cuda()
        optimizer = init_optim(args.optim, model.parameters(), args.lr, args.weight_decay)
        scheduler = lr_scheduler.MultiStepLR(optimizer, milestones=args.stepsize, gamma=args.gamma)
        start_epoch = args.start_epoch

    else:
        if use_gpu:
            model = nn.DataParallel(model).cuda()
        optimizer = init_optim(args.optim, model.parameters(), args.lr, args.weight_decay)
        scheduler = lr_scheduler.MultiStepLR(optimizer, milestones=args.stepsize, gamma=args.gamma)
        start_epoch = args.start_epoch

    if args.evaluate:
        print("Evaluate only")
        test(model, queryloader, galleryloader, use_gpu, multi_query=args.multi_query, mode=args.mode)
        return

    start_time = time.time()
    train_time = 0
    best_rank1 = -np.inf
    best_mAP = -np.inf
    best_epoch = 0
    print("==> Start training")

    for epoch in range(start_epoch, args.max_epoch):
        start_train_time = time.time()
        train(epoch, model, criterion_xent, criterion_htri, optimizer, trainloader, use_gpu)
        train_time += round(time.time() - start_train_time)
        
        scheduler.step()
        
        if (epoch+1) > args.start_eval and args.eval_step > 0 and (epoch+1) % args.eval_step == 0 or (epoch+1) == args.max_epoch:
            print("==> Test")
            rank1, mAP = test(model, queryloader, galleryloader, use_gpu, multi_query=args.multi_query, mode=args.mode)
            is_best = (mAP > best_mAP) or ((mAP == best_mAP) and (rank1 > best_rank1))
            
            if is_best:
                best_rank1 = rank1
                best_mAP = mAP
                best_epoch = epoch + 1

            if use_gpu:
                state_dict = model.module.state_dict()
            else:
                state_dict = model.state_dict()
            
            save_checkpoint({
                'state_dict': state_dict,
                'rank1': rank1,
                'epoch': epoch,
                'best_rank1': best_rank1,
                'best_epoch': best_epoch,
                'args' : args,
                'optimizer': optimizer.state_dict(),
            }, is_best, osp.join(args.save_dir, 'checkpoint_ep' + str(epoch+1) + '.pth.tar'))

    print("==> Best mAP {:.1%}, Rank-1 {:.1%}, achieved at epoch {}".format(best_mAP, best_rank1, best_epoch))

    elapsed = round(time.time() - start_time)
    elapsed = str(datetime.timedelta(seconds=elapsed))
    train_time = str(datetime.timedelta(seconds=train_time))
    print("Finished. Total elapsed time (h:m:s): {}. Training time (h:m:s): {}.".format(elapsed, train_time))


def train(epoch, model, criterion_xent, criterion_htri, optimizer, trainloader, use_gpu):
    losses = AverageMeter()
    losses_xent_loss = AverageMeter()
    losses_htri_loss = AverageMeter()
    batch_time = AverageMeter()
    data_time = AverageMeter()

    model.train()

    end = time.time()
    for batch_idx, (imgs, pids, _) in enumerate(trainloader):
        data_time.update(time.time() - end)
        
        if use_gpu:
            imgs, pids = imgs.cuda(), pids.cuda()
        
        outputs, features = model(imgs)
        if args.htri_only:
            if isinstance(features, tuple):
                loss = DeepSupervision(criterion_htri, features, pids)
            else:
                loss = criterion_htri(features, pids)
        else:
            if isinstance(outputs, tuple):
                xent_loss = DeepSupervision(criterion_xent, outputs, pids)
            else:
                xent_loss = criterion_xent(outputs, pids)
            
            if isinstance(features, tuple):
                htri_loss = DeepSupervision(criterion_htri, features, pids)
            else:
                htri_loss = criterion_htri(features, pids)
            
            loss = xent_loss + htri_loss
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        batch_time.update(time.time() - end)

        losses.update(loss.item(), pids.size(0))
        losses_xent_loss.update(xent_loss.item(), pids.size(0))
        losses_htri_loss.update(htri_loss.item(), pids.size(0))

        if (batch_idx+1) % args.print_freq == 0:
            print('Epoch: [{0}][{1}/{2}]\t'
                  'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                  'Data {data_time.val:.4f} ({data_time.avg:.4f})\t'    
                  'Loss_xent {loss_xent.val:.4f}\t'
                  'Loss_htri {loss_htri.val:.4f}\t'
                  'Loss {loss.val:.4f} ({loss.avg:.4f})\t'.format(
                   epoch+1, batch_idx+1, len(trainloader), batch_time=batch_time,
                   data_time=data_time, loss_xent=losses_xent_loss, 
                   loss_htri=losses_htri_loss, loss=losses))
        
        end = time.time()


def get_max_feature(qf, f):
    qf, _ = torch.max(torch.cat([qf, f], dim=0), dim=0, keepdim=True)
    return qf

def get_avg_feature(qf, f, count):
    qf = qf * ((count-1) / count) + f / count
    return qf

def test(model, queryloader, galleryloader, use_gpu, ranks=[1, 5, 10, 20], multi_query=False, mode='max'):
    batch_time = AverageMeter()

    model.eval()

    with torch.no_grad():
        qf, q_pids, q_camids = [], [], []

        if multi_query:
            qf_dict = {}
            qf_count_dict = {}
            dict_idx = -1
            for batch_idx, (imgs, pids, camids) in enumerate(queryloader):
                if use_gpu:
                    imgs = imgs.cuda()

                end = time.time()
                #ifeatures, _, _, _ = model(imgs)
                features = model(imgs)
                features = features.data.cpu()
                # features = [1,2,3,4,5,6,7,8,9,10,11,12]

                pids = pids.numpy()
                camids = camids.numpy()
                for i, (feature, pid, camid) in enumerate(zip(features, pids, camids)):
                    p_c_key = str(pid) + '_' + str(camid)
                    if p_c_key in qf_dict:
                        idx = qf_dict[p_c_key]
                        # qf[idx].append(feature)
                        if mode == 'max':
                            qf[idx] = get_max_feature(qf[idx], feature.unsqueeze(0))
                        elif mode == 'avg':
                            qf_count_dict[p_c_key] = qf_count_dict[p_c_key] + 1
                            qf[idx] = get_avg_feature(qf[idx], feature.unsqueeze(0), qf_count_dict[p_c_key])
                        else:
                            print('Mode error')
                    else:
                        dict_idx = dict_idx + 1
                        qf_dict[p_c_key] = dict_idx
                        qf_count_dict[p_c_key] = 1.0
                        # qf.append([feature])
                        # qf.append(feature.unsqueeze(0).data.cpu())
                        qf.append(feature.unsqueeze(0))
                        q_pids.append(pid)
                        q_camids.append(camid)

                    # print (p_c_key)
                    # print (pid)
                    # print (camid)

                batch_time.update(time.time() - end)

                # features = features.data.cpu()
                # qf.append(features)
                # q_pids.extend(pids)
                # q_camids.extend(camids)
        else:
            for batch_idx, (imgs, pids, camids) in enumerate(queryloader):
                if use_gpu:
                    imgs = imgs.cuda()

                end = time.time()
                #features, _, _, _ = model(imgs)
                features = model(imgs)
                batch_time.update(time.time() - end)

                features = features.data.cpu()
                qf.append(features)
                q_pids.extend(pids)
                q_camids.extend(camids)

        qf = torch.cat(qf, 0)
        q_pids = np.asarray(q_pids)
        q_camids = np.asarray(q_camids)

        print("Extracted features for query set, obtained {}-by-{} matrix".format(qf.size(0), qf.size(1)))

        gf, g_pids, g_camids = [], [], []
        for batch_idx, (imgs, pids, camids) in enumerate(galleryloader):
            if use_gpu:
                imgs = imgs.cuda()
            
            end = time.time()
            #features, _, _, _ = model(imgs)
            features = model(imgs)
            batch_time.update(time.time() - end)

            features = features.data.cpu()
            gf.append(features)
            g_pids.extend(pids)
            g_camids.extend(camids)
        gf = torch.cat(gf, 0)
        g_pids = np.asarray(g_pids)
        g_camids = np.asarray(g_camids)

        print("Extracted features for gallery set, obtained {}-by-{} matrix".format(gf.size(0), gf.size(1)))
    
    print("==> BatchTime(s)/BatchSize(img): {:.3f}/{}".format(batch_time.avg, args.test_batch))

    m, n = qf.size(0), gf.size(0)
    distmat = torch.pow(qf, 2).sum(dim=1, keepdim=True).expand(m, n) + \
              torch.pow(gf, 2).sum(dim=1, keepdim=True).expand(n, m).t()
    distmat.addmm_(1, -2, qf, gf.t())
    distmat = distmat.numpy()

    print("Computing CMC and mAP")
    cmc, mAP = evaluate(distmat, q_pids, g_pids, q_camids, g_camids, use_metric_cuhk03=args.use_metric_cuhk03)

    print("Results ----------")
    print("mAP: {:.1%}".format(mAP))
    print("CMC curve")
    for r in ranks:
        print("Rank-{:<3}: {:.1%}".format(r, cmc[r-1]))
    print("------------------")

    return cmc[0], mAP


if __name__ == '__main__':
    main()

