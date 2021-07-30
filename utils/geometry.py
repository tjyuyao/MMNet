"""Provides functions that manipulate boxes and points"""

import torch
import math

import numpy as np
import torch.nn.functional as F


def center(box):
    r"""Calculates centers, (x, y), of box (N, 4)"""
    x_center = box[:, 0] + (box[:, 2] - box[:, 0]) // 2
    y_center = box[:, 1] + (box[:, 3] - box[:, 1]) // 2
    return torch.stack((x_center, y_center)).t().to(box.device)


def buildOneHot(index, featsShape):
    res = torch.zeros(featsShape[0]*featsShape[1])
    res[index] = 1
    return res


def getNonNeighborhoodIndices(kps, featsShape, originalShape, kernel=7):

    x, y = kps.copy()

    h, w = featsShape
    x *= w/float(originalShape[0])
    y *= h/float(originalShape[1])

    xfloat, xmin = math.modf(x)
    yfloat, ymin = math.modf(y)

    index_list = []

    for i in range(w):
        for j in range(h):
            if abs(i-x) < kernel/2 and abs(j-y) < kernel/2:
                continue
            index_list.append([j, i])
    return index_list


def BilinearInterpolate(kps, correlationMap, originalShape):
    # x: width, y:height
    x, y = kps.copy()
    h, w = correlationMap.shape[:2]
    correlationMap = correlationMap.view(h, w, -1)

    map2D = torch.zeros(h, w)
    x *= w/float(originalShape[0])
    y *= h/float(originalShape[1])

    xfloat, xmin = math.modf(x)
    yfloat, ymin = math.modf(y)
    xmin = int(xmin)
    ymin = int(ymin)

    res1 = correlationMap[ymin, xmin]


    
    if ymin+1 <= h-1:
        res1 = (1-yfloat) * res1 + (yfloat) * correlationMap[ymin+1, xmin]
    
    res2 = None
    if xmin+1 <= w-1:
        res2 = correlationMap[ymin, xmin+1]
    if ymin+1 <= h-1 and xmin+1 <= w-1:
        res2 = (1-yfloat) * res2 + (yfloat) * correlationMap[ymin+1, xmin+1]
    
    if res2 is None:
        return res1
    else:
        return (1-xfloat) * res1 + (xfloat)*res2


def BilinearInterpolateParalleled(kps_list, correlationMap, originalShape):
    # x: width, y:height
    cur_kps_list = kps_list.permute(0, 2, 1)
    device = kps_list.device

    b, h, w = correlationMap.shape[:3]
    correlationMap = correlationMap.view(b, h, w, -1)

    _, kpsNum, _ = cur_kps_list.shape
    #

    map2D = torch.zeros(b, h, w)

    cur_kps_list = cur_kps_list * \
        torch.Tensor([2*1.0/float(originalShape[0]), 2 *
                      1.0/float(originalShape[1])]).to(device)
    cur_kps_list -= 1.0
    cur_kps_list = cur_kps_list.unsqueeze(0)

    # 1,5,50,2

    res = F.grid_sample(correlationMap.permute(0, 3, 1, 2), cur_kps_list.permute(
        1, 0, 2, 3), mode='bilinear').permute(2, 0, 3, 1).squeeze(0)
    # 5,50,70

    return res


def findNearestPoint(kps, featsShape, originalShape):

    x, y = kps.copy()
    h, w = featsShape

    x *= w/float(originalShape[0])
    y *= h/float(originalShape[1])
    xfloat, xmin = math.modf(x)
    yfloat, ymin = math.modf(y)

    if xfloat <= 0.5:
        if yfloat <= 0.5:
            return [xmin, ymin]
        else:
            return [xmin, min(h-1, ymin+1)]
    elif yfloat < 0.5:
        return [min(w-1, xmin+1), ymin]
    else:
        return [min(w-1, xmin+1), min(h-1, ymin+1)]


def getBlurredGT(kps, featsShape, originalShape):
    x, y = kps.copy()
    h, w = featsShape

    x *= w/float(originalShape[0])
    y *= h/float(originalShape[1])

    xfloat, xmin = math.modf(x)
    yfloat, ymin = math.modf(y)
    xmin = int(xmin)
    ymin = int(ymin)

    map2D = torch.zeros(h, w)

    nodeList = []
    invDistanceList = []

    minimal = 1e-5

    nodeList.append([xmin, ymin])
    d = math.sqrt(xfloat**2+yfloat**2)
    invDistanceList.append(1/max(d, minimal))

    if ymin+1 <= h-1:
        nodeList.append([xmin, ymin+1])
        d = math.sqrt(xfloat**2+(1-yfloat)**2)
        invDistanceList.append(1/max(d, minimal))
    if xmin+1 <= w-1:
        nodeList.append([xmin+1, ymin])
        d = math.sqrt((1-xfloat)**2+yfloat**2)
        invDistanceList.append(1/max(d, minimal))
    if ymin+1 <= h-1 and xmin+1 <= w-1:
        nodeList.append([xmin+1, ymin+1])
        d = math.sqrt((1-xfloat)**2+(1-yfloat)**2)
        invDistanceList.append(1/max(d, minimal))

    invDistanceList = np.array(invDistanceList)
    sumD = np.sum(invDistanceList)

    normalizedD = invDistanceList/sumD

    for idx, i in enumerate(nodeList):
        x, y = i
        map2D[y, x] = normalizedD[idx]

    hsfilter = gaussian2d(3)
    map2D = F.conv2d(map2D.view(1, 1, h, w),
                     hsfilter.unsqueeze(0).unsqueeze(0), padding=1).view(h, w)
    return map2D.view(-1)


def getBlurredGTParalleled(kps_list, featsShape, originalShape):
    cur_kps_list = kps_list.permute(0, 2, 1)
    device = kps_list.device
    h, w = featsShape

    b, kpsNum, _ = cur_kps_list.shape
    map2D = torch.zeros(b, kpsNum, h*w).to(device)

    cur_kps_list = cur_kps_list * \
        torch.Tensor([w/float(originalShape[0]), h /
                      float(originalShape[1])]).to(device)

    xfloored = torch.floor(cur_kps_list[:, :, 0])
    xceiled = torch.ceil(cur_kps_list[:, :, 0])
    xceiled = torch.where(xceiled >= w-1, xfloored, xceiled)

    yfloored = torch.floor(cur_kps_list[:, :, 1])
    yceiled = torch.ceil(cur_kps_list[:, :, 1])
    yceiled = torch.where(yceiled >= h-1, yfloored, yceiled)

    xremained = torch.remainder(cur_kps_list[:, :, 0], 1)
    yremained = torch.remainder(cur_kps_list[:, :, 1], 1)

    distanceMat = torch.stack([torch.sqrt(xremained*xremained+yremained*yremained),
                               torch.sqrt(
                                   (xceiled-cur_kps_list[:, :, 0])*(xceiled-cur_kps_list[:, :, 0])+yremained*yremained),
                               torch.sqrt(xremained*xremained+(yceiled -
                                                               cur_kps_list[:, :, 1])*(yceiled-cur_kps_list[:, :, 1])),
                               torch.sqrt((xceiled-cur_kps_list[:, :, 0])*(xceiled-cur_kps_list[:, :, 0])+(yceiled-cur_kps_list[:, :, 1])*(yceiled-cur_kps_list[:, :, 1]))]).to(device).permute(1, 2, 0)
    indexMat = torch.stack([yceiled*w+xceiled,
                            yceiled*w+xfloored,
                            yfloored*w+xceiled,
                            yfloored*w+xfloored]).to(device).to(torch.int64).permute(1, 2, 0)
    # 4,5,50

    minimal = 1e-5
    distanceMat[distanceMat < minimal] = minimal
    invDMat = 1.0/distanceMat

    #4, 5, 50

    sumD = torch.sum(invDMat, dim=0)
    invDMat /= sumD

    # 5, 50
    hsfilter = gaussian2d(3).to(device)
    maps_all = []
    for i in range(b):
        maps_b = []
        for j in range(kpsNum):
            map2D = torch.zeros(h*w).to(device)
            for k in range(4):
                map2D[indexMat[i, j, k]] += invDMat[i, j, k]
            map2D = F.conv2d(map2D.view(1, 1, h, w),
                             hsfilter.unsqueeze(0).unsqueeze(0), padding=1).view(h, w)
            maps_b.append(map2D)

        maps_b = torch.stack(maps_b).to(device)
        maps_all.append(maps_b)
    maps_all = torch.stack(maps_all).view(b, kpsNum, -1)

    return maps_all


def predict_kps(src_kps, confidence_ts, originalShape):
    r"""Transfer keypoints by nearest-neighbour assignment"""

    src_kps = src_kps.cpu().detach().numpy()
    #trg_kps = trg_kps.cpu().detach().numpy()
    h, w = confidence_ts.shape[:2]

    confidence_ts_tmp = confidence_ts.view(h, w, -1)
    _, trg_argmax_idx = torch.max(confidence_ts_tmp, dim=2)

    featsShape = [h, w]

    pred_kps_x = []
    pred_kps_y = []

    for i in range(len(src_kps[0])):
        x, y = findNearestPoint(
            [src_kps[0, i], src_kps[1, i]], featsShape, originalShape=originalShape)

        map_bilinear = BilinearInterpolate(
            [src_kps[0, i], src_kps[1, i]], confidence_ts, originalShape=originalShape).unsqueeze(0)

        _, pred = torch.max(map_bilinear, dim=1)

        pred_x = int(pred % w)
        pred_y = int(pred / w)

        pred_x *= originalShape[0]/w
        pred_y *= originalShape[1]/h

        pred_kps_x.append(pred_x)
        pred_kps_y.append(pred_y)


    return [pred_kps_x, pred_kps_y]


def neighbours(box, kps):
    r"""Returns boxes in one-hot format that covers given keypoints"""
    box_duplicate = box.unsqueeze(2).repeat(1, 1, len(kps.t())).transpose(0, 1)
    kps_duplicate = kps.unsqueeze(1).repeat(1, len(box), 1)

    xmin = kps_duplicate[0].ge(box_duplicate[0])
    ymin = kps_duplicate[1].ge(box_duplicate[1])
    xmax = kps_duplicate[0].le(box_duplicate[2])
    ymax = kps_duplicate[1].le(box_duplicate[3])

    nbr_onehot = torch.mul(torch.mul(xmin, ymin), torch.mul(xmax, ymax)).t()
    n_neighbours = nbr_onehot.sum(dim=1)

    return nbr_onehot, n_neighbours


def gaussian2d(side=7):
    r"""Returns 2-dimensional gaussian filter"""
    dim = [side, side]

    siz = torch.LongTensor(dim)
    sig_sq = (siz.float()/2/2.354).pow(2)
    siz2 = (siz-1)/2

    x_axis = torch.arange(-siz2[0], siz2[0] +
                          1).unsqueeze(0).expand(dim).float()
    y_axis = torch.arange(-siz2[1], siz2[1] +
                          1).unsqueeze(1).expand(dim).float()

    gaussian = torch.exp(-(x_axis.pow(2)/2 /
                           sig_sq[0] + y_axis.pow(2)/2/sig_sq[1]))
    gaussian = gaussian / gaussian.sum()

    return gaussian
