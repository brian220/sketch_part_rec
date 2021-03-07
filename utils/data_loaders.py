# -*- coding: utf-8 -*-
#
# Developed by Chaoyu Huang 
# email: b608390.cs04@nctu.edu.tw
# Lot's of code reference to Pix2Vox: 
# https://github.com/hzxie/Pix2Vox

import cv2
import json
import numpy as np
import os
import random
import scipy.io
import scipy.ndimage
import sys
import torch.utils.data.dataset

from datetime import datetime as dt
from enum import Enum, unique

from pyntcloud import PyntCloud


@unique
class DatasetType(Enum):
    TRAIN = 0
    TEST = 1
    VAL = 2


# //////////////////////////////// = End of DatasetType Class Definition = ///////////////////////////////// #

def init_pointcloud_loader(num_points):
    Z = np.random.rand(num_points) + 1.
    h = np.random.uniform(10., 214., size=(num_points,))
    w = np.random.uniform(10., 214., size=(num_points,))
    X = (w - 111.5) / 60. * -Z # focal length: 60
    Y = (h - 111.5) / 60. * Z
    X = np.reshape(X, (-1, 1))
    Y = np.reshape(Y, (-1, 1))
    Z = np.reshape(Z, (-1, 1))
    XYZ = np.concatenate((X, Y, Z), 1)
    return XYZ.astype('float32')

def sample_spherical(n_points):
    vec = np.random.rand(n_points, 3) * 2. - 1.
    vec /= np.linalg.norm(vec, axis=1, keepdims=True)
    pc = vec * .3 + np.array([[6.462339e-04,  9.615256e-04, -7.909229e-01]])
    return pc.astype('float32')

# xyz is a single pcl
def np_rotate(xyz, xangle=0, yangle=0, inverse=False):
    '''
    Rotate input pcl along x and y axes using numpy
    args:
            xyz: float, (N_PTS,3), numpy array; input point cloud
            xangle, yangle: float, (); angles by which pcl has to be rotated, 
                                    in radians
    returns:
            xyz: float, (N_PTS,3); rotated point clooud
    '''
    rotmat = np.eye(3)
    rotmat=rotmat.dot(np.array([
	    [1.0,0.0,0.0],
	    [0.0,np.cos(xangle),-np.sin(xangle)],
	    [0.0,np.sin(xangle),np.cos(xangle)],
	    ]))
    rotmat=rotmat.dot(np.array([
	    [np.cos(yangle),0.0,-np.sin(yangle)],
	    [0.0,1.0,0.0],
	    [np.sin(yangle),0.0,np.cos(yangle)],
	    ]))
    if inverse:
	    rotmat = np.linalg.inv(rotmat)
    return xyz.dot(rotmat)

class ShapeNetDataset(torch.utils.data.dataset.Dataset):
    """ShapeNetDataset class used for PyTorch DataLoader"""
    def __init__(self, dataset_type, file_list, init_num_points, proj_num_views, grid_h, grid_w, rec_model, transforms=None):
        self.dataset_type = dataset_type
        self.file_list = file_list
        self.init_num_points = init_num_points
        self.proj_num_views = proj_num_views
        self.rec_model = rec_model
        self.grid_h = grid_h
        self.grid_w = grid_w
        self.transforms = transforms

    def __len__(self):
        return len(self.file_list)

    def __getitem__(self, idx):
        taxonomy_name, sample_name, rendering_images, \
        model_gt, edge_gt, model_x, model_y, \
        init_point_cloud, ground_truth_point_cloud = self.get_datum(idx)

        if self.transforms:
            rendering_images = self.transforms(rendering_images)

        return (taxonomy_name, sample_name, rendering_images,
                model_gt, edge_gt, model_x, model_y, 
                init_point_cloud, ground_truth_point_cloud)

    def get_datum(self, idx):
        taxonomy_name = self.file_list[idx]['taxonomy_name']
        sample_name = self.file_list[idx]['sample_name']
        rendering_image_paths = self.file_list[idx]['rendering_images']
        depth_image_paths = self.file_list[idx]['depth_images']
        ground_truth_point_cloud_path = self.file_list[idx]['point_cloud']
        radian_x = self.file_list[idx]['radian_x']
        radian_y = self.file_list[idx]['radian_y']

        # get data of rendering images (sample 1 image from paths)
        if self.dataset_type == DatasetType.TRAIN:
            rand_id = random.randint(0, len(rendering_image_paths) - 1)
            selected_rendering_image_path = rendering_image_paths[rand_id]
        else:
        # test, valid with the first image
            selected_rendering_image_path = rendering_image_paths[1]
        
        # read the test, train image
        rendering_images = []
        rendering_image = cv2.imread(selected_rendering_image_path, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.

        if self.rec_model == 'PSGN_FC':
            rendering_image = cv2.resize(rendering_image, (self.grid_h, self.grid_w))
        elif self.rec_model == 'GRAPHX':
            rendering_image = cv2.resize(rendering_image, (224, 224))
        else:
            print('Invalid model name, please check the config.py (NET_WORK.REC_MODEL)')
            sys.exit(2)
        
        # rendering_image = cv2.cvtColor(rendering_image, cv2.COLOR_BGR2RGB)
        rendering_image = np.expand_dims(rendering_image, -1)

        if len(rendering_image.shape) < 1:
            print('[FATAL] %s It seems that there is something wrong with the image file %s' %
                     (dt.now(), image_path))
            sys.exit(2)
        rendering_images.append(rendering_image)
        
        # read the ground truth proj images and views (multi-views)
        model_gt = []
        edge_gt = []
        model_x = []
        model_y = []
        for idx in range(0, self.proj_num_views):
            # read the proj imgs
            proj_path = depth_image_paths[idx]
            ip_proj = cv2.imread(proj_path)[:,:,0]
            ip_proj = cv2.resize(ip_proj, (self.grid_h,self.grid_w))
            ip_proj[ip_proj<254] = 1
            ip_proj[ip_proj>=254] = 0

            # get the gt edge proj by cv2.findContours
            edge_proj = ip_proj
            cv2.findContours(edge_proj, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

            edge_proj = edge_proj.astype(np.float32)
            ip_proj = ip_proj.astype(np.float32)
            model_gt.append(ip_proj)
            edge_gt.append(edge_proj)

            # read the views
            model_x.append(radian_x[idx])
            model_y.append(radian_y[idx])
        
        # get data of point cloud
        _, suffix = os.path.splitext(ground_truth_point_cloud_path)

        if suffix == '.ply':
            ground_truth_point_cloud = PyntCloud.from_file(ground_truth_point_cloud_path)
            ground_truth_point_cloud = np.array(ground_truth_point_cloud.points).astype(np.float32)
            
        elif suffix == '.npy':
            ground_truth_point_cloud = np.load(ground_truth_point_cloud_path).astype(np.float32)

        # convert to np array
        rendering_images = np.array(rendering_images).astype(np.float32)
        model_gt = np.array(model_gt).astype(np.float32)
        edge_gt = np.array(edge_gt).astype(np.float32)
        model_x = np.array(model_x).astype(np.float32)
        model_y = np.array(model_y).astype(np.float32)
        
        return (taxonomy_name, sample_name, rendering_images,
                model_gt, edge_gt, model_x, model_y,
                init_pointcloud_loader(self.init_num_points), ground_truth_point_cloud)


# //////////////////////////////// = End of ShapeNetDataset Class Definition = ///////////////////////////////// #


class ShapeNetDataLoader:
    def __init__(self, cfg):
        self.dataset_taxonomy = None
        
        self.rendering_image_path_template = cfg.DATASETS.SHAPENET.RENDERING_PATH
        self.depth_image_path_template = cfg.DATASETS.SHAPENET.DEPTH_PATH
        self.point_cloud_path_template = cfg.DATASETS.SHAPENET.POINT_CLOUD_PATH
        self.view_path_template = cfg.DATASETS.SHAPENET.VIEW_PATH

        self.class_name = cfg.DATASET.CLASS
        self.total_views = cfg.DATASET.TOTAL_VIEWS
        self.init_num_points = cfg.GRAPHX.NUM_INIT_POINTS
        self.proj_num_views = cfg.PROJECTION.NUM_VIEWS
        self.grid_h = cfg.PROJECTION.GRID_H 
        self.grid_w = cfg.PROJECTION.GRID_W
        self.rec_model = cfg.NETWORK.REC_MODEL
        
        # Load all taxonomies of the dataset
        with open(cfg.DATASETS.SHAPENET.TAXONOMY_FILE_PATH, encoding='utf-8') as file:
            self.dataset_taxonomy = json.loads(file.read())
        
        # Get the class data (in dict) from taxonomy
        self.dataset_class_data_taxonomy = self.dataset_taxonomy[self.class_name]

    def get_dataset(self, dataset_type, transforms=None):
        taxonomy_folder_name = self.dataset_class_data_taxonomy['taxonomy_id']
        print('[INFO] %s Collecting files of Taxonomy[ID=%s, Name=%s]' %
                (dt.now(), self.dataset_class_data_taxonomy['taxonomy_id'], self.class_name))
        
        samples = []
        if dataset_type == DatasetType.TRAIN:
            samples = self.dataset_class_data_taxonomy['train']
        elif dataset_type == DatasetType.TEST:
            samples = self.dataset_class_data_taxonomy['test']
        elif dataset_type == DatasetType.VAL:
            samples = self.dataset_class_data_taxonomy['val']

        files = self.get_files_of_taxonomy(taxonomy_folder_name, samples)

        print('[INFO] %s Complete collecting files of the dataset. Total files: %d.' % (dt.now(), len(files)))
        return ShapeNetDataset(dataset_type, files, self.init_num_points, self.proj_num_views, self.grid_h, self.grid_w, self.rec_model, transforms)
        
    def get_files_of_taxonomy(self, taxonomy_folder_name, samples):
        files_of_taxonomy = []
        for sample_idx, sample_name in enumerate(samples):
            # Get file paths of pointcloud
            point_cloud_file_path = self.point_cloud_path_template % (taxonomy_folder_name, sample_name)
            if not os.path.exists(point_cloud_file_path):
                print('[WARN] %s Ignore sample %s/%s since point cloud file not exists.' %
                      (dt.now(), taxonomy_folder_name, sample_name))
                continue
            
            # Get file paths of rendering images
            rendering_image_indexes = range(self.total_views)
            rendering_image_file_paths = []
            for image_idx in rendering_image_indexes:
                img_file_path = self.rendering_image_path_template % (taxonomy_folder_name, sample_name, image_idx)
                if not os.path.exists(img_file_path):
                    continue
                rendering_image_file_paths.append(img_file_path)
            
            if len(rendering_image_file_paths) == 0:
                print('[WARN] %s Ignore sample %s/%s since image files not exists.' %
                      (dt.now(), taxonomy_folder_name, sample_name))
                continue

            # Get file list of depth images
            depth_image_indexes = range(self.total_views)
            depth_image_file_paths = []
            for image_idx in depth_image_indexes:
                depth_image_file_path = self.depth_image_path_template % (taxonomy_folder_name, sample_name, image_idx)
                if not os.path.exists(depth_image_file_path):
                    continue
                depth_image_file_paths.append(depth_image_file_path)

            if len(depth_image_file_paths) == 0:
                print('[WARN] %s Ignore sample %s/%s since depth files not exists.' %
                      (dt.now(), taxonomy_folder_name, sample_name))
                continue

            # Get views of objects (azimuth, elevation)
            view_path = self.view_path_template % (taxonomy_folder_name, sample_name)
            if not os.path.exists(view_path):
                print('[WARN] %s Ignore sample %s/%s since view file not exists.' %
                      (dt.now(), taxonomy_folder_name, sample_name))
                continue
            
            angles = []
            with open(view_path) as f:
                angles = [item.split('\n')[0] for item in f.readlines()]
            
            radian_x = [] # azi
            radian_y = [] # ele
            for angle in angles:
                angle_x = float(angle.split(' ')[0])
                angle_y = float(angle.split(' ')[1])
                # convert angles to radians
                radian_x.append(angle_x*np.pi/180.)
                radian_y.append((angle_y - 90.)*np.pi/180.) # original model face direction: z, change to x
                
            # Append to the list of rendering images
            files_of_taxonomy.append({
                'taxonomy_name': taxonomy_folder_name,
                'sample_name': sample_name,
                'rendering_images': rendering_image_file_paths,
                'depth_images' : depth_image_file_paths,
                'point_cloud': point_cloud_file_path,
                'radian_x' : radian_x,
                'radian_y' : radian_y
            })

            # Report the progress of reading dataset
            # if sample_idx % 500 == 499 or sample_idx == n_samples - 1:
            #     print('[INFO] %s Collecting %d of %d' % (dt.now(), sample_idx + 1, n_samples))
            
        return files_of_taxonomy


# /////////////////////////////// = End of ShapeNetDataLoader Class Definition = /////////////////////////////// #


DATASET_LOADER_MAPPING = {
    'ShapeNet': ShapeNetDataLoader
    # 'Pascal3D': Pascal3dDataLoader, # not implemented
    # 'Pix3D': Pix3dDataLoader # not implemented
} 

