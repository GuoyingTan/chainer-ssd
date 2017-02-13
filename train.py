#! /usr/bin/env python3

import argparse
import cv2
import numpy as np
import os
import xml.etree.ElementTree as ET

import chainer
from chainer import training
from chainer.training import extensions

from ssd import SSD300
from multibox import MultiBoxEncoder
import voc


class VOCDataset(chainer.dataset.DatasetMixin):

    def __init__(self, root, size, encoder):
        self.root = root
        self.size = size
        self.encoder = encoder

        self.images = [
            l.strip() for l in open(os.path.join(
                self.root, 'ImageSets', 'Main', 'trainval.txt'))]

    def __len__(self):
        return len(self.images)

    def get_example(self, i):
        x = cv2.imread(
            os.path.join(
                self.root, 'JPEGImages', self.images[i] + '.jpg'),
            cv2.IMREAD_COLOR)
        h, w, _ = x.shape
        x = cv2.resize(x, (self.size, self.size)).astype(np.float32)
        x -= (103.939, 116.779, 123.68)
        x = x.transpose(2, 0, 1)

        boxes = list()
        classes = list()
        tree = ET.parse(os.path.join(
            self.root, 'Annotations', self.images[i] + '.xml'))
        for child in tree.getroot():
            if not child.tag == 'object':
                continue
            bndbox = child.find('bndbox')
            xmin = float(bndbox.find('xmin').text)
            ymin = float(bndbox.find('ymin').text)
            xmax = float(bndbox.find('xmax').text)
            ymax = float(bndbox.find('ymax').text)
            boxes.append((
                (xmin + xmax) / (2 * w),
                (ymin + ymax) / (2 * h),
                (xmax - xmin) / w,
                (ymax - ymin) / h))
            classes.append(voc.names.index(child.find('name').text))
        loc, conf = self.encoder.encode(
            np.array(boxes), np.array(classes))

        return x, loc, conf


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root')
    parser.add_argument('--batchsize', type=int, default=32)
    parser.add_argument('--loaderjob', type=int, default=2)
    parser.add_argument('--gpu', type=int, default=-1)
    parser.add_argument('--out', default='result')
    args = parser.parse_args()

    size = 300
    aspect_ratios = ((2,), (2, 3), (2, 3), (2, 3), (2,), (2,))

    multibox_encoder = MultiBoxEncoder(
        n_scale=6,
        variance=(0.1, 0.2),
        grids=(38, 19, 10, 5, 3, 1),
        aspect_ratios=aspect_ratios)

    model = SSD300(
        n_class=20,
        n_anchors=multibox_encoder.n_anchors)
    if args.gpu >= 0:
        chainer.cuda.get_device(args.gpu).use()
        model.to_gpu()

    train = VOCDataset(args.root, size, multibox_encoder)

    train_iter = chainer.iterators.MultiprocessIterator(
        train, args.batchsize, n_processes=args.loaderjob)

    optimizer = chainer.optimizers.MomentumSGD(lr=0.01, momentum=0.9)
    optimizer.setup(model)

    updater = training.StandardUpdater(train_iter, optimizer, device=args.gpu)
    trainer = training.Trainer(updater, (1000, 'iteration'), args.out)

    snapshot_interval = 100, 'iteration'
    log_interval = 10, 'iteration'

    trainer.extend(extensions.dump_graph('main/loss'))
    trainer.extend(extensions.snapshot(), trigger=snapshot_interval)
    trainer.extend(extensions.snapshot_object(
        model, 'model_iter_{.updater.iteration}'), trigger=snapshot_interval)
    trainer.extend(extensions.LogReport(trigger=log_interval))
    trainer.extend(extensions.observe_lr(), trigger=log_interval)
    trainer.extend(
        extensions.PrintReport(['epoch', 'iteration', 'main/loss', 'lr']),
        trigger=log_interval)
    trainer.extend(extensions.ProgressBar(update_interval=10))

    trainer.run()