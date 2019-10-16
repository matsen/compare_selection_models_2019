#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Created on Wed Jan 30 12:06:58 2019

@author: zacharysethna
"""


import numpy as np
import os
import logging
logging.getLogger('tensorflow').disabled = True
from keras.layers import Dense, Input, Lambda
from keras.models import Sequential,Model
from keras.models import load_model as lm
from keras import regularizers
import keras.backend as K
import keras as kr

class Sonia(object):
	"""Class used to infer a Q selection model.


	Attributes
	----------
	features : ndarray
		Array of feature lists. Each list contains individual subfeatures which
		all must be satisfied.
	features_dict : dict
		Dictionary keyed by tuples of the feature lists. Values are the index
		of the feature, i.e. self.features[self.features_dict[tuple(f)]] = f.
	constant_features : list
		List of feature strings to not update parameters during learning. These
		features are still used to compute energies (not currently used)
	model_params : ndarray
		Array of energies corresponding to each feature in features
	data_seqs : list
		Data sequences used to infer selection model. Note, each 'sequence'
		is a list where the first element is the CDR3 sequence which is
		followed by any V or J genes.
	gen_seqs : list
		Sequences from generative distribution used to infer selection model.
		Note, each 'sequence' is a list where the first element is the CDR3
		sequence which is followed by any V or J genes.
	data_seq_features : list
		Lists of features that data_seqs project onto
	gen_seq_features : list
		Lists of features that gen_seqs project onto
	data_marginals : ndarray
		Array of the marginals of each feature over data_seqs
	gen_marginals : ndarray
		Array of the marginals of each feature over gen_seqs
	model_marginals : ndarray
		Array of the marginals of each feature over the model weighted gen_seqs
	L1_converge_history : list
		L1 distance between data_marginals and model_marginals at each
		iteration.
	chain_type : str
		Type of receptor. This specification is used to determine gene names
		and allow integrated OLGA sequence generation. Options: 'humanTRA',
		'humanTRB' (default), 'humanIGH', and 'mouseTRB'.
	max_iterations : int
		Maximum number of iterations per learning epoch
	step_size : float
		Step size for the update of the model energies at each iteration.
	converge_threshold : float
		Terminates learning epoch if the L1 distance is below the threshold
	l2_reg : float or None
		L2 regularization. If None (default) then no regularization.
	v : ndarray
		'Velocity' of a inference iteration. Gives the changes to each energy in
		model_params for the current iteration.
	drag : float
		Drag coefficient for the inference iterations. The smaller the drag,
		the faster the parameter learning, but the convergence is less stable.

	Methods
	----------
	seq_feature_proj(feature, seq)
		Determines if a feature matches/is found in a sequence.

	find_seq_features(seq, features = None)
		Determines all model features of a sequence.

	compute_seq_energy(seq_features = None, seq = None)
		Computes the energy, as determined by the model, of a sequence.

	compute_marginals(self, features = None, seq_model_features = None, seqs = None, use_flat_distribution = False)
		Computes the marginals of features over a set of sequences.

	infer_selection(self, max_iterations = None, step_size = None, drag = None, initialize = False, initialize_from_zero = False, zero_flipped_indices = True, l2_reg = None, sub_sample_frac = None, converge_threshold = None)
		Infers model parameters (energies for each feature).

	update_model(self, add_data_seqs = [], add_gen_seqs = [], add_features = [], remove_features = [], add_constant_features = [], auto_update_marginals = False, auto_update_seq_features = False)
		Updates model by adding/removing model features or data/generated seqs.
		Marginals and seq_features can also be updated.

	add_generated_seqs(self, num_gen_seqs = 0, reset_gen_seqs = True)
		Generates synthetic sequences using OLGA and adds them to gen_seqs.

	plot_model_learning(self, save_name = None)
		Plots current marginal scatter plot as well as L1 convergence history.

	save_model(self, save_dir, attributes_to_save = None)
		Saves the model.

	load_model(self, load_dir, load_seqs = True)
		Loads a model.

	"""

	def __init__(self, features = [], constant_features = [], data_seqs = [], gen_seqs = [], chain_type = 'humanTRB', load_model = None,sample_gen=False,size_sample=int(5e4),custom_pgen_model=None):
		self.features = np.array(features)
		self.feature_dict = {tuple(f): i for i, f in enumerate(self.features)}
		#self.constant_features =  constant_features
		self.model_params = np.zeros(len(self.features))
		self.data_seqs = []
		self.gen_seqs = []
		self.data_seq_features = []
		self.gen_seq_features = []
		self.data_marginals = np.zeros(len(features))
		self.gen_marginals = np.zeros(len(features))
		self.model_marginals = np.zeros(len(features))
		self.L1_converge_history = []
		self.pseudo_count = .5
		self.l2_reg = 0.

		default_chain_types = {'humanTRA': 'human_T_alpha', 'human_T_alpha': 'human_T_alpha', 'humanTRB': 'human_T_beta', 'human_T_beta': 'human_T_beta', 'humanIGH': 'human_B_heavy', 'human_B_heavy': 'human_B_heavy', 'mouseTRB': 'mouse_T_beta', 'mouse_T_beta': 'mouse_T_beta'}
		if chain_type not in default_chain_types.keys():
			print 'Unrecognized chain_type (not a default OLGA model). Please specify one of the following options: humanTRA, humanTRB, humanIGH, or mouseTRB.'
			return None
		self.chain_type = default_chain_types[chain_type]
		self.custom_model_folder=custom_pgen_model
		if load_model is not None:
			self.load_model(load_model)
		else:
			self.update_model(add_data_seqs = data_seqs, add_gen_seqs = gen_seqs)
			self.update_model_structure(initialize=True)
			self.L1_converge_history = []
		if sample_gen: self.add_generated_seqs(size_sample,custom_model_folder=custom_pgen_model)

		self.max_iterations = 100
		self.step_size = 0.1 #step size
		self.converge_threshold = 1e-3
		self.amino_acids = 'ARNDCQEGHILKMFPSTWYV'
		self.v = np.zeros(len(self.features))
		self.drag = 0.0
		
		
	def reject_bad_features(self,threshold=5):
		""" Keeps only the features associated with marginals that have a high enough count in the gen pool, in order to 
		avoid numerical instability in the inference. Restricted only to VJ genes.

		Parameters
		----------
		threshold : int
			minimum number of counts in datasets
		"""
		if not self.include_genes: return True # skip rejection if don't have v,j genes
		self.gen_marginals = self.compute_marginals(seq_model_features = self.gen_seq_features, use_flat_distribution = True)
		n_gen=(self.gen_marginals*len(self.gen_seq_features)).astype(np.int) # get counts
		selection=n_gen>threshold
		selection[:np.sum([(q[0][0]=='a' or q[0][0]=='l')for q in self.features])]=True # throw away only vj bad components
		self.features=self.features[selection]
		self.feature_dict = {tuple(f): i for i, f in enumerate(self.features)}
		self.update_model_structure(initialize=True)
		self.update_model(auto_update_seq_features=True)

		return True 

	def update_model_structure(self,output_layer=[],input_layer=[],initialize=False):
		""" Defines the model structure and compiles it.

		Parameters
		----------
		structure : Sequential Model Keras
			structure of the model
		initialize: bool
			if True, it initializes to linear model, otherwise it updates to new structure

		"""
		length_input=np.max([len(self.features),1])
		if initialize:
			input_layer = Input(shape=(length_input,))
			self.model_structure = Dense(1,use_bias=False,activation='linear', activity_regularizer=regularizers.l2(self.l2_reg))(input_layer) #normal glm model
		else: self.model_structure=output_layer

		# Define model
		clipped_out=Lambda(lambda x: K.clip(x,-4,8))(self.model_structure)
		self.model = Model(inputs=input_layer, outputs=clipped_out)

		self.optimizer = kr.optimizers.RMSprop()
		self.model.compile(optimizer=self.optimizer, loss=loss,metrics=[likelihood])
		self.model_params = self.model.get_weights()
		return True 

	def seq_feature_proj(self, feature, seq):
		"""Checks if a sequence matches all subfeatures of the feature list

		Parameters
		----------
		feature : list
			List of individual subfeatures the sequence must match
		seq : list
			CDR3 sequence and any associated genes

		Returns
		-------
		bool
			True if seq matches feature else False.

		"""
		try:
			for f in feature:
				if f[0] == 'a': #Amino acid subfeature
					if seq[0][int(f[2:])] != f[1]:
						return False
				elif f[0] == 'v' or f[0] == 'j': #Gene subfeature
						if not any([[int(x) for x in f[1:].split('-')] == [int(y) for y in gene.lower().split(f[0])[-1].split('-')] for gene in seq[1:] if f[0] in gene.lower()]):
							if not any([[int(x) for x in f[1:].split('-')] == [int(gene.lower().split(f[0])[-1].split('-')[0])] for gene in seq[1:] if f[0] in gene.lower()]): #Checks for gene-family match if specified as such
								return False
				elif f[0] == 'l': #CDR3 length subfeature
					if len(seq[0]) != int(f[1:]):
						return False
		except: #All ValueErrors and IndexErrors return False
			return False

		return True

	def find_seq_features(self, seq, features = None):
		"""Finds which features match seq


		Parameters
		----------
		seq : list
			CDR3 sequence and any associated genes
		features : ndarray
			Array of feature lists. Each list contains individual subfeatures which
			all must be satisfied.

		Returns
		-------
		seq_features : list
			Indices of features seq projects onto.

		"""
		if features is None:
			features = self.features
		seq_features = []
		for feature_index, feature_lst in enumerate(features):
			if self.seq_feature_proj(feature_lst, seq):
				seq_features += [feature_index]

		return seq_features

	def compute_energy(self,seqs_features):
		"""Computes the energy of a list of sequences according to the model.


		Parameters
		----------
		seqs_features : list
			list of encoded sequences into sonia features.

		Returns
		-------
		E : float
			Energies of seqs according to the model.

		"""
		seqs_features_enc=self.encode_data(seqs_features)
		return self.model.predict(seqs_features_enc)[:, 0]

	def compute_marginals(self, features = None, seq_model_features = None, seqs = None, use_flat_distribution = False, output_dict = False):
		"""Computes the marginals of each feature over sequences.

		Computes marginals either with a flat distribution over the sequences
		or weighted by the model energies. Note, finding the features of each
		sequence takes time and should be avoided if it has already been done.
		If computing marginals of model features use the default setting to
		prevent searching for the model features a second time. Similarly, if
		seq_model_features has already been determined use this to avoid
		recalculating it.

		Parameters
		----------
		features : list or None
			List of features. This does not need to match the model
			features. If None (default) the model features will be used.
		seq_features_all : list
			Indices of model features seqs project onto.
		seqs : list
			List of sequences to compute the feature marginals over. Note, each
			'sequence' is a list where the first element is the CDR3 sequence
			which is followed by any V or J genes.
		use_flat_distribution : bool
			Marginals will be computed using a flat distribution (each seq is
			weighted as 1) if True. If False, the marginals are computed using
			model weights (each sequence is weighted as exp(-E) = Q). Default
			is False.
		output_dict : bool
			The marginal output is either formatted as an array or a dictionary.
			The default output is an ndarray (False). If True, the dictionary
			is keyed by tuple-formatted features.

		Returns
		-------
		marginals : ndarray or dict
			Marginals of model features over seqs.

		"""

		if seq_model_features is None:  # if model features are not given
			if seqs is not None:  # if sequences are given, get model features from them
				seq_model_features = [self.find_seq_features(seq) for seq in seqs]
			else:   # if no sequences are given, sequences features is empty
				seq_model_features = []

		if len(seq_model_features) == 0:  # if no model features are given, return empty array
			return np.array([])

		if features is not None and seqs is not None:  # if a different set of features for the marginals is given
			seq_compute_features = [self.find_seq_features(seq, features = features) for seq in seqs]
		else:  # if no features or no sequences are provided, compute marginals using model features
			seq_compute_features = seq_model_features

		marginals = np.zeros(len(self.features))
		energy_sum = 0
		if not use_flat_distribution:
			energies = self.compute_energy(seq_compute_features)
			qs= np.exp(-energies)
			for seq_features,t in zip(seq_compute_features,qs):
				marginals[seq_features] += t
				energy_sum += t
		else:
			for seq_features in seq_compute_features:
				marginals[seq_features] += 1
				energy_sum += 1

		marginals = marginals / energy_sum
		return marginals

	def infer_selection(self, max_iterations = 20, step_size = None,  initialize = True, initialize_from_zero = False,  l2_reg = None, converge_threshold = None,batch_size=5000):
		"""Infer model parameters, i.e. energies for each model feature.


		Parameters
		----------
		max_iterations : int
			Maximum number of learning epochs
		step_size : float
			Step size for the update of the model energies at each iteration.
		intialize : bool
			Resets model before inferring model_params
		initialize_from_zero : bool
			Resets model parameters to zeros (instead of matching mean field)
		l2_reg : float or None
			L2 regularization. If None (default) then no regularization.
		converge_threshold: float
			stop inference once reached a specific L1 dist
		batch_size: int
			size of the batch in the inference


		Attributes set
		--------------
		model_params : array
			Parameters of the model
		model_marginals : array
			Marginals over the generated sequences, reweighted by the model.
		L1_converge_history : list
			L1 distance between data_marginals and model_marginals at each
			iteration.

		"""

		self.l2_reg = 0.0001
		if max_iterations is not None:
			self.max_iterations = max_iterations
		if converge_threshold is not None:
			self.converge_threshold = converge_threshold
		self.l2_reg = l2_reg

		if initialize or initialize_from_zero:

			if initialize_from_zero:
				self.model_params = np.zeros(len(self.features))
			else:
				self.model_params = np.array([-np.log10(self.data_marginals[i]/x) if x > 0 and self.data_marginals[i] > 0 else 0. for i, x in enumerate(self.gen_marginals)])
			self.update_model(auto_update_marginals=True) #make sure all attributes are updated
			self.L1_converge_history = []#[sum(abs(self.data_marginals - self.model_marginals))]
			self.model_params_history = []
			self.v = np.zeros(len(self.features))

			# prepare data
			self.X = np.array(self.data_seq_features+self.gen_seq_features)
			self.Y = np.concatenate([np.zeros(len(self.data_seq_features)), np.ones(len(self.gen_seq_features))])
			
			shuffle = np.random.permutation(len(self.X)) # shuffle
			self.X=self.X[shuffle]
			self.Y=self.Y[shuffle]

		len_features = len(self.features)
		computeL1_dist = computeL1(self)
		callbacks = [computeL1_dist]
		self.learning_history = self.model.fit(self.encode_data(self.X), self.Y, epochs=max_iterations, batch_size=batch_size,
										  validation_split=0.2, verbose=0, callbacks=callbacks)
		self.L1_converge_history = computeL1_dist.L1_history
		self.model_params = self.model.get_weights()
		#self.gauge_energies()
		self.update_model(auto_update_marginals=True)
		
	def gauge_energies(self):
		"""Gauge energies (i.e. model parameters).

		Gauging must be done in a model-feature specific fashion, so this is
		a dummy function.
		"""

		pass

	def update_model(self, add_data_seqs = [], add_gen_seqs = [], add_features = [], remove_features = [], add_constant_features = [], auto_update_marginals = False, auto_update_seq_features = False):
		"""Updates the model attributes

		This method is used to add/remove model features or data/generated
		sequences. These changes will be propagated through the class to update
		any other attributes that need to match (e.g. the marginals or
		seq_features).


		Parameters
		----------
		add_data_seqs : list
			List of CDR3 sequences to add to data_seq pool.
		add_gen_seqs : list
			List of CDR3 sequences to add to data_seq pool.
		add_gen_seqs : list
			List of CDR3 sequences to add to data_seq pool.
		add_features : list
			List of feature lists to add to self.features
		remove_featurese : list
			List of feature lists and/or indices to remove from self.features
		add_constant_features : list
			List of feature lists to add to constant features. (Not currently used)
		auto_update_marginals : bool
			Specifies to update marginals.
		auto_update_seq_features : bool
			Specifies to update seq features.

		Attributes set
		--------------
		features : list
			List of model features
		data_seq_features : list
			Features data_seqs have been projected onto.
		gen_seq_features : list
			Features gen_seqs have been projected onto.
		data_marginals : ndarray
			Marginals over the data sequences for each model feature.
		gen_marginals : ndarray
			Marginals over the generated sequences for each model feature.
		model_marginals : ndarray
			Marginals over the generated sequences, reweighted by the model,
			for each model feature.

		"""

		self.data_seqs += [[seq,'',''] if type(seq)==str else seq for seq in add_data_seqs] #add_data_seqs
		self.gen_seqs += [[seq,'',''] if type(seq)==str else seq for seq in add_gen_seqs] #add_gen_seqs
		#self.constant_features += add_constant_features

		if len(remove_features) > 0:
			indices_to_keep = [i for i, feature_lst in enumerate(self.features) if feature_lst not in remove_features and i not in remove_features]
			self.features = self.features[indices_to_keep]
			self.update_model_structure(initialize=True)
			self.feature_dict = {tuple(f): i for i, f in enumerate(self.features)}

		if len(add_features) > 0:
			self.features = np.append(self.features, add_features)
			self.update_model_structure(initialize=True)
			self.feature_dict = {tuple(f): i for i, f in enumerate(self.features)}
			
		if (len(add_data_seqs + add_features + remove_features) > 0 or auto_update_seq_features) and len(self.features)>0:
			self.data_seq_features = [self.find_seq_features(seq) for seq in self.data_seqs]

		if (len(add_data_seqs + add_features + remove_features) > 0 or auto_update_marginals > 0) and len(self.features)>0:
			self.data_marginals = self.compute_marginals(seq_model_features = self.data_seq_features, use_flat_distribution = True)
			#self.inv_I = np.linalg.inv(self.compute_data_fisher_info())
			#self.floating_features = self.data_marginals > 0

		if (len(add_gen_seqs + add_features + remove_features) > 0 or auto_update_seq_features) and len(self.features)>0:
			self.gen_seq_features = [self.find_seq_features(seq) for seq in self.gen_seqs]

		if (len(add_gen_seqs + add_features + remove_features) > 0 or auto_update_marginals) and len(self.features)>0:
			self.gen_marginals = self.compute_marginals(seq_model_features = self.gen_seq_features, use_flat_distribution = True)
			self.model_marginals = self.compute_marginals(seq_model_features = self.gen_seq_features)

		if len(self.data_seqs) > 0 and len(self.gen_seqs) > 0:
			self.data_marginals[[self.data_marginals[i] == 0 and self.gen_marginals[i] > 0 for i in range(len(self.data_marginals))]] = self.pseudo_count/float(len(self.data_seqs))

	def add_generated_seqs(self, num_gen_seqs = 0, reset_gen_seqs = True, custom_model_folder = None):
		"""Generates MonteCarlo sequences for gen_seqs using OLGA.

		Only generates seqs from a V(D)J model. Requires the OLGA package
		(pip install olga).

		Parameters
		----------
		num_gen_seqs : int or float
			Number of MonteCarlo sequences to generate and add to the specified
			sequence pool.
		custom_model_folder : str
			Path to a folder specifying a custom IGoR formatted model to be
			used as a generative model. Folder must contain 'model_params.txt'
			and 'model_marginals.txt'

		Attributes set
		--------------
		gen_seqs : list
			MonteCarlo sequences drawn from a VDJ recomb model
		gen_seq_features : list
			Features gen_seqs have been projected onto.

		"""
		#Load OLGA for seq generation
		import olga.load_model as olga_load_model
		import olga.sequence_generation as seq_gen

		#Load generative model
		if custom_model_folder is None:
			main_folder = os.path.join(os.path.dirname(olga_load_model.__file__), 'default_models', self.chain_type)
		else:
			main_folder = custom_model_folder

		params_file_name = os.path.join(main_folder,'model_params.txt')
		marginals_file_name = os.path.join(main_folder,'model_marginals.txt')
		V_anchor_pos_file = os.path.join(main_folder,'V_gene_CDR3_anchors.csv')
		J_anchor_pos_file = os.path.join(main_folder,'J_gene_CDR3_anchors.csv')

		if not os.path.isfile(params_file_name) or not os.path.isfile(marginals_file_name):
			print 'Cannot find specified custom generative model files: ' + '\n' + params_file_name + '\n' + marginals_file_name
			print 'Exiting sequence generation...'
			return None
		if not os.path.isfile(V_anchor_pos_file):
			V_anchor_pos_file = os.path.join(os.path.dirname(olga_load_model.__file__), 'default_models', self.chain_type, 'V_gene_CDR3_anchors.csv')
		if not os.path.isfile(J_anchor_pos_file):
			J_anchor_pos_file = os.path.join(os.path.dirname(olga_load_model.__file__), 'default_models', self.chain_type, 'J_gene_CDR3_anchors.csv')

		if self.chain_type.endswith('TRA'):
			genomic_data = olga_load_model.GenomicDataVJ()
			genomic_data.load_igor_genomic_data(params_file_name, V_anchor_pos_file, J_anchor_pos_file)
			generative_model = olga_load_model.GenerativeModelVJ()
			generative_model.load_and_process_igor_model(marginals_file_name)
			sg_model = seq_gen.SequenceGenerationVJ(generative_model, genomic_data)
		else:
			genomic_data = olga_load_model.GenomicDataVDJ()
			genomic_data.load_igor_genomic_data(params_file_name, V_anchor_pos_file, J_anchor_pos_file)
			generative_model = olga_load_model.GenerativeModelVDJ()
			generative_model.load_and_process_igor_model(marginals_file_name)
			sg_model = seq_gen.SequenceGenerationVDJ(generative_model, genomic_data)

		#Generate sequences
		seqs = [[seq[1], genomic_data.genV[seq[2]][0].split('*')[0], genomic_data.genJ[seq[3]][0].split('*')[0]] for seq in [sg_model.gen_rnd_prod_CDR3() for _ in range(int(num_gen_seqs))]]

		if reset_gen_seqs: #reset gen_seqs if needed
			self.gen_seqs = []
		#Add to specified pool(s)
		self.update_model(add_gen_seqs = seqs)

	def plot_model_learning(self, save_name = None):
		"""Plots L1 convergence curve and marginal scatter.

		Parameters
		----------
		save_name : str or None
			File name to save output figure. If None (default) does not save.

		"""

		import matplotlib.pyplot as plt

		min_for_plot = 1/(10.*np.power(10, np.ceil(np.log10(len(self.data_seqs)))))
		fig = plt.figure(figsize =(13, 4))
		fig.add_subplot(131)
		fig.subplots_adjust(left=0.1, bottom = 0.13, top = 0.91, right = 0.97, wspace = 0.3, hspace = 0.15)
		plt.loglog(range(1, len(self.L1_converge_history)+1), self.L1_converge_history, 'k', linewidth = 2)
		plt.xlabel('Iteration', fontsize = 13)
		plt.ylabel('L1 Distance', fontsize = 13)

		plt.legend(frameon = False, loc = 2)
		plt.title('L1 Distance convergence', fontsize = 15)
		
		fig.add_subplot(132)

		plt.loglog(self.data_marginals, self.gen_marginals, 'r.', alpha = 0.2, markersize=1)
		plt.loglog(self.data_marginals, self.model_marginals, 'b.', alpha = 0.2, markersize=1)
		plt.loglog([],[], 'r.', label = 'Raw marginals')
		plt.loglog([],[], 'b.', label = 'Model adjusted marginals')

		plt.loglog([min_for_plot, 2], [min_for_plot, 2], 'k--', linewidth = 0.5)
		plt.xlim([min_for_plot, 1])
		plt.ylim([min_for_plot, 1])

		plt.xlabel('Marginals over data', fontsize = 13)
		plt.ylabel('Marginals over generated sequences', fontsize = 13)
		plt.legend(loc = 2, fontsize = 10)
		plt.title('Marginal Scatter', fontsize = 15)
		
		fig.add_subplot(133)
		plt.title('Likelihood', fontsize = 15)
		plt.plot(self.learning_history.history['likelihood'],label='train',c='k')
		plt.plot(self.learning_history.history['val_likelihood'],label='validation',c='r')
		plt.legend(fontsize = 10)
		plt.xlabel('Iteration', fontsize = 13)
		plt.ylabel('Likelihood', fontsize = 13)

		if save_name is not None:
			fig.savefig(save_name)

	def save_model(self, save_dir, attributes_to_save = None):
		"""Saves model parameters and sequences

		Parameters
		----------
		save_dir : str
			Directory name to save model attributes to.
		attributes_to_save: list
			name of attributes to save

		"""

		if attributes_to_save is None:
			attributes_to_save = ['model_params', 'data_seqs', 'gen_seqs', 'L1_converge_history']

		if os.path.isdir(save_dir):
			print 'The directory ' + save_dir + ' already exists. Overwrite existing model'
		else:
			os.mkdir(save_dir)

		if 'data_seqs' in attributes_to_save:
			with open(os.path.join(save_dir, 'data_seqs.tsv'), 'w') as data_seqs_file:
				data_seqs_file.write('Sequence;Genes\tFeatures\n')
				data_seqs_file.write('\n'.join([';'.join(seq)+ '\t'+ ';'.join([','.join(self.features[f]) for f in self.data_seq_features[i]]) for i, seq in enumerate(self.data_seqs)]))

		if 'gen_seqs' in attributes_to_save:
			with open(os.path.join(save_dir, 'gen_seqs.tsv'), 'w') as gen_seqs_file:
				gen_seqs_file.write('Sequence;Genes\tFeatures\n')
				gen_seqs_file.write('\n'.join([';'.join(seq) + '\t' + ';'.join([','.join(self.features[f]) for f in self.gen_seq_features[i]]) for i, seq in enumerate(self.gen_seqs)]))

		if 'L1_converge_history' in attributes_to_save:
			with open(os.path.join(save_dir, 'L1_converge_history.tsv'), 'w') as L1_file:
				L1_file.write('\n'.join([str(x) for x in self.L1_converge_history]))

		if 'model_params' in attributes_to_save:
			features_file = open(os.path.join(save_dir, 'features.tsv'), 'w')
			features_file.write('Feature\n')
			for i, p in enumerate(self.features):
				features_file.write(';'.join(p) + '\n')
			features_file.close()
			self.model.save(os.path.join(save_dir, 'model.h5'))
		return None

	def load_model(self, load_dir, load_seqs = True):
		"""Loads model from directory.

		Parameters
		----------
		load_dir : str
			Directory name to load model attributes from.

		"""
		if not os.path.isdir(load_dir):
			print 'Directory for loading model does not exist (' + load_dir + ')'
			print 'Exiting...'
			return None

		if os.path.isfile(os.path.join(load_dir, 'features.tsv')):
			features = []
			features_file = open(os.path.join(load_dir, 'features.tsv'), 'r')
			for i, line in enumerate(features_file):
				split_line = line.strip()
				if i == 0: #skip header
					continue
				try:
					features.append(split_line.split(';'))
				except:
					pass
			features_file.close()
			self.model = lm(os.path.join(load_dir, 'model.h5'), custom_objects={'loss': loss,'likelihood':likelihood})
			self.model_params=self.model.get_weights()
			self.features = np.array(features)
			self.feature_dict = {tuple(f): i for i, f in enumerate(self.features)}
		else:
			print 'Cannot find model.tsv  --  no features or model parameters loaded.'

		if os.path.isfile(os.path.join(load_dir, 'data_seqs.tsv')) and load_seqs:
			with open(os.path.join(load_dir, 'data_seqs.tsv'), 'r') as data_seqs_file:
				self.data_seqs = []
				self.data_seq_features = []
				for line in data_seqs_file.read().strip().split('\n')[1:]:
					split_line = line.split('\t')
					self.data_seqs.append(split_line[0].split(';'))
					self.data_seq_features.append([self.feature_dict[tuple(f.split(','))] for f in split_line[1].split(';') if tuple(f.split(',')) in self.feature_dict])
		else:
			print 'Cannot find data_seqs.tsv  --  no data seqs loaded.'

		if os.path.isfile(os.path.join(load_dir, 'gen_seqs.tsv')) and load_seqs:
			with open(os.path.join(load_dir, 'gen_seqs.tsv'), 'r') as gen_seqs_file:
				self.gen_seqs = []
				self.gen_seq_features = []
				for line in gen_seqs_file.read().strip().split('\n')[1:]:
					split_line = line.split('\t')
					self.gen_seqs.append(split_line[0].split(';'))
					self.gen_seq_features.append([self.feature_dict[tuple(f.split(','))] for f in split_line[1].split(';') if tuple(f.split(',')) in self.feature_dict])
		else:
			print 'Cannot find gen_seqs.tsv  --  no generated seqs loaded.'

		self.update_model(auto_update_marginals = True)

		if os.path.isfile(os.path.join(load_dir, 'L1_converge_history.tsv')):
			with open(os.path.join(load_dir, 'L1_converge_history.tsv'), 'r') as L1_file:
				self.L1_converge_history = [float(line.strip()) for line in L1_file if len(line.strip())>0]
		else:
			self.L1_converge_history = []
			print 'Cannot find L1_converge_history.tsv  --  no L1 convergence history loaded.'

		return None

	def encode_data(self,seq_features):
		if len(seq_features[0])==0: seq_features=[seq_features]
		length_input=len(self.features)
		data=np.array(seq_features)
		data_enc = np.zeros((len(data), length_input), dtype=np.int8)
		for i in range(len(data_enc)): data_enc[i][data[i]] = 1
		return data_enc

gamma=1e-1
def loss(y_true, y_pred):
	data= K.sum((-y_pred)*(1.-y_true))/K.sum(1.-y_true)
	gen= K.log(K.sum(K.exp(-y_pred)*y_true))-K.log(K.sum(y_true))
	reg= K.exp(gen)-1.
	return gen-data+gamma*reg*reg


def likelihood(y_true, y_pred):
	data= K.sum((-y_pred)*(1.-y_true))/K.sum(1.-y_true)
	gen= K.log(K.sum(K.exp(-y_pred)*y_true))-K.log(K.sum(y_true))
	return gen-data

class computeL1(kr.callbacks.Callback):
			
			def __init__(self, sonia):
				self.data_marginals = sonia.data_marginals
				self.sonia=sonia
				self.len_features=len(sonia.features)
				self.gen_enc = self.sonia.X[self.sonia.Y.astype(np.bool)]
				self.encoded_data=self.sonia.encode_data(self.gen_enc)

			def on_train_begin(self, logs={}):
				self.L1_history = []
				self.L1_history.append(np.sum(np.abs(self.return_model_marginals() - self.data_marginals)))
				print "Initial L1 dist: ", self.L1_history[-1]

			def return_model_marginals(self):
				marginals = np.zeros(self.len_features)
				qs = np.exp(-self.model.predict(self.encoded_data)[:, 0])  
				for i in range(len(self.gen_enc)):
					marginals[self.gen_enc[i]] += qs[i]
				return marginals / np.sum(qs)

			def on_epoch_end(self, epoch, logs={}):
				curr_loss = logs.get('loss')
				curr_loss_val = logs.get('val_loss')
				self.L1_history.append(np.sum(np.abs(self.return_model_marginals() - self.data_marginals)))
				print "epoch = ", epoch, " loss = ", np.around(curr_loss, decimals=4) , " val_loss = ", np.around(curr_loss_val, decimals=4), " L1 dist: ", np.around(self.L1_history[-1], decimals=4)