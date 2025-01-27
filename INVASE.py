'''
Written by Jinsung Yoon
Date: Jan 1th 2019
INVASE: Instance-wise Variable Selection using Neural Networks Implementation on Synthetic Datasets
Reference: J. Yoon, J. Jordon, M. van der Schaar, "IINVASE: Instance-wise Variable Selection using Neural Networks," International Conference on Learning Representations (ICLR), 2019.
Paper Link: https://openreview.net/forum?id=BJg_roAcK7
Contact: jsyoon0823@g.ucla.edu

---------------------------------------------------

Instance-wise Variable Selection (INVASE) - with baseline networks
'''

#%% Necessary packages
# 1. Keras
from keras.layers import Input, Dense, Multiply
from keras.layers import BatchNormalization
from keras.models import Sequential, Model
from keras.optimizers import Adam
from keras import regularizers
from keras import backend as K

# 2. Others
import tensorflow as tf
import numpy as np
from sklearn.metrics import roc_auc_score, average_precision_score, accuracy_score

#%% Define INVASE class
class INVASE():
    
    # 1. Initialization
    '''
    x_train: training samples
    data_type: Syn1 to Syn 6
    '''
    def __init__(self, x_train, data_type, n_epoch, is_logging_enabled=True, learning_rate=0.0001):
        self.is_logging_enabled = is_logging_enabled
        
        self.batch_size = min(1000, x_train.shape[0])      # Batch size
        self.epochs = n_epoch        # Epoch size (large epoch is needed due to the policy gradient framework)
        self.tau = 0.1             # Hyper-parameter for the number of selected features 

        self.input_shape = x_train.shape[1]     # Input dimension
        
        # Activation. (For Syn1 and 2, relu, others, selu)
        self.activation = 'relu' if data_type in ['Syn1','Syn2'] else 'selu' # Why SeLu?

        # Use Adam optimize
        optimizer = Adam(lr=learning_rate)
        
        # Build and compile the predictor (critic)
        self.predictor = self.build_base_network()
        # Use categorical cross entropy as the loss
        self.predictor.compile(loss='categorical_crossentropy', optimizer=optimizer, metrics=['acc'])

        # Build the selector (actor)
        self.selector = self.build_selector()
        # Use custom loss (my loss)
        self.selector.compile(loss=self.my_loss, optimizer=optimizer)

        # Build and compile the baseline
        self.baseline = self.build_base_network()
        # Use categorical cross entropy as the loss
        self.baseline.compile(loss='categorical_crossentropy', optimizer=optimizer, metrics=['acc'])

    #%% Custom loss definition
    def my_loss(self, y_true, y_pred):
        
        # dimension of the features
        d = y_pred.shape[1]        
        
        # Put all three in y_true 
        # 1. selected probability
        sel_prob = y_true[:,:d]
        # 2. predictor output
        dis_prob = y_true[:,d:(d+2)]
        # 3. baseline output
        val_prob = y_true[:,(d+2):(d+4)]
        # 4. ground truth
        y_final = y_true[:,(d+4):]        
        
        # A1. Compute the rewards of the actor network
        Reward1 = tf.reduce_sum(y_final * tf.log(dis_prob + 1e-8), axis = 1)  
        
        # A2. Compute the rewards of the actor network
        Reward2 = tf.reduce_sum(y_final * tf.log(val_prob + 1e-8), axis = 1)  

        # Difference is the rewards
        Reward = Reward1 - Reward2

        # B. Policy gradient loss computation. 
        loss1 = Reward * tf.reduce_sum(sel_prob * K.log(y_pred + 1e-8) + (1-sel_prob) * K.log(1-y_pred + 1e-8), axis = 1) - self.tau * tf.reduce_mean(y_pred, axis = 1)
        
        # C. Maximize the loss1
        loss = tf.reduce_mean(-loss1)

        return loss

    #%% Selector (Actor)
    def build_selector(self):

        model = Sequential()
        
        model.add(Dense(100, activation=self.activation, name='s/dense1', kernel_regularizer=regularizers.l2(1e-3), input_dim = self.input_shape))
        model.add(Dense(100, activation=self.activation, name='s/dense2', kernel_regularizer=regularizers.l2(1e-3)))
        model.add(Dense(self.input_shape, activation = 'sigmoid', name='s/dense3', kernel_regularizer=regularizers.l2(1e-3)))

        feature = Input(shape=(self.input_shape,), dtype='float32')
        selection_prob = model(feature)

        return Model(feature, selection_prob)
        
    #%% Baseline & predictor
    def build_base_network(self):

        model = Sequential()
                
        model.add(Dense(200, activation=self.activation, name='dense1', kernel_regularizer=regularizers.l2(1e-3), input_dim = self.input_shape)) 
        model.add(BatchNormalization())
        model.add(Dense(200, activation=self.activation, name = 'dense2', kernel_regularizer=regularizers.l2(1e-3)))
        model.add(BatchNormalization())
        model.add(Dense(2, activation ='softmax', name = 'dense3', kernel_regularizer=regularizers.l2(1e-3)))
        
        feature = Input(shape=(self.input_shape,), dtype='float32')       
        prob = model(feature)

        return Model(feature, prob)

    #%% Sampling the features based on the output of the generator
    def Sample_M(self, gen_prob):
        
        # Shape of the selection probability
        n = gen_prob.shape[0]
        d = gen_prob.shape[1]
                
        # Sampling
        samples = np.random.binomial(1, gen_prob, (n,d))
        
        return samples

  #%% Training procedure
    def train(self, x_train, y_train):

        # For each epoch (actually iterations!)
        for epoch in range(self.epochs):

            #%% Train predictor
            # Select a random batch of samples
            idx = np.random.randint(0, x_train.shape[0], self.batch_size)
            x_batch = x_train[idx,:]
            y_batch = y_train[idx,:]

            # Generate a batch of probabilities of feature selection
            sel_prob = self.selector.predict(x_batch)
            
            # Sampling the features based on the generated probability and overlaying mask on input
            sel_mask = self.Sample_M(sel_prob)
            sel_feat = Multiply()([x_batch, sel_mask])     
            
            # Compute the prediction of the critic based on the sampled features (used for selector training)
            dis_prob = self.predictor.predict(sel_feat)

            # Train the predictor
            d_loss = self.predictor.train_on_batch(sel_feat, y_batch)

            #%% Train the baseline

            # Compute the prediction of the critic based on the sampled features (used for selector training)
            val_prob = self.baseline.predict(x_batch)

            # Train the predictor
            v_loss = self.baseline.train_on_batch(x_batch, y_batch)
            
            #%% Train selector
            # Use three things as the y_true: sel_prob, dis_prob, and ground truth (y_batch)
            y_batch_final = np.concatenate( (sel_prob, np.asarray(dis_prob), np.asarray(val_prob), y_batch), axis = 1 )

            # Train the selector
            g_loss = self.selector.train_on_batch(x_batch, y_batch_final)

            #%% Plot the progress
            dialog = 'Epoch: '+str(epoch)+', d_loss (Acc)): '+str(d_loss[1])+', v_loss (Acc): '+str(v_loss[1])+', g_loss: '+str(np.round(g_loss,4))

            if epoch % 100 == 0:
                print(dialog)
    
    #%% Selected Features        
    def output(self, x_train):
        
        gen_prob = self.generator.predict(x_train)
        
        return np.asarray(gen_prob)
     
    #%% Prediction Results 
    def get_prediction(self, x_train, m_train):
        
        val_prediction = self.valfunction.predict(x_train)
        
        dis_prediction = self.discriminator.predict([x_train, m_train])
        
        return np.asarray(val_prediction), np.asarray(dis_prediction)


#%% Main Function
if __name__ == '__main__':
        
    # Data generation function import
    from Data_Generation import generate_data
    
    #%% Parameters
    # Synthetic data type    
    idx = 5
    data_sets = ['Syn1','Syn2','Syn3','Syn4','Syn5','Syn6']
    data_type = data_sets[idx]
    
    # Data output can be either binary (Y) or Probability (Prob)
    data_out_sets = ['Y','Prob']
    data_out = data_out_sets[0]
    
    # Number of Training and Testing samples
    train_N = 10000
    test_N = 10000
    
    # Seeds (different seeds for training and testing)
    train_seed = 0
    test_seed = 1
        
    #%% Data Generation (Train/Test)
    def create_data(data_type, data_out): 
        
        x_train, y_train, g_train = generate_data(n = train_N, data_type = data_type, seed = train_seed, out = data_out)  
        x_test,  y_test,  g_test  = generate_data(n = test_N,  data_type = data_type, seed = test_seed,  out = data_out)  
    
        return x_train, y_train, g_train, x_test, y_test, g_test
    
    x_train, y_train, g_train, x_test, y_test, g_test = create_data(data_type, data_out)

    #%% 
    # 1. INVASE Class call
    INVASE_Alg = INVASE(x_train, data_type)
    
    # 2. Algorithm training
    INVASE_Alg.train(x_train, y_train)
    
    # 3. Get the selection probability on the testing set
    Sel_Prob_Test = INVASE_Alg.output(x_test)
    
    # 4. Selected features
    score = 1.*(Sel_Prob_Test > 0.5)
    
    # 5. Prediction
    val_predict, dis_predict = INVASE_Alg.get_prediction(x_test, score)
    
    #%% Performance Metrics
    def performance_metric(score, g_truth):

        n = len(score)
        Temp_TPR = np.zeros([n,])
        Temp_FDR = np.zeros([n,])
        
        for i in range(n):
    
            # TPR    
            TPR_Nom = np.sum(score[i,:] * g_truth[i,:])
            TPR_Den = np.sum(g_truth[i,:])
            Temp_TPR[i] = 100 * float(TPR_Nom)/float(TPR_Den+1e-8)
        
            # FDR
            FDR_Nom = np.sum(score[i,:] * (1-g_truth[i,:]))
            FDR_Den = np.sum(score[i,:])
            Temp_FDR[i] = 100 * float(FDR_Nom)/float(FDR_Den+1e-8)
    
        return np.mean(Temp_TPR), np.mean(Temp_FDR), np.std(Temp_TPR), np.std(Temp_FDR)
    
    #%% Output
    TPR_mean, FDR_mean, TPR_std, FDR_std = performance_metric(score, g_test)
        
    print('TPR mean: ' + str(np.round(TPR_mean,1)) + '\%, ' + 'TPR std: ' + str(np.round(TPR_std,1)) + '\%, '  )
    print('FDR mean: ' + str(np.round(FDR_mean,1)) + '\%, ' + 'FDR std: ' + str(np.round(FDR_std,1)) + '\%, '  )
        
    #%% Prediction Results
    Predict_Out = np.zeros([20,3,2])    

    for i in range(20):
        
        # different test seed
        test_seed = i+2
        _, _, _, x_test, y_test, _ = create_data(data_type, data_out)  
                
        # 1. Get the selection probability on the testing set
        Sel_Prob_Test = INVASE_Alg.output(x_test)
    
        # 2. Selected features
        score = 1.*(Sel_Prob_Test > 0.5)
    
        # 3. Prediction
        val_predict, dis_predict = INVASE_Alg.get_prediction(x_test, score)
        
        # 4. Prediction Results
        Predict_Out[i,0,0] = roc_auc_score(y_test[:,1], val_predict[:,1])
        Predict_Out[i,1,0] = average_precision_score(y_test[:,1], val_predict[:,1])
        Predict_Out[i,2,0] = accuracy_score(y_test[:,1], 1. * (val_predict[:,1]>0.5) )
    
        Predict_Out[i,0,1] = roc_auc_score(y_test[:,1], dis_predict[:,1])
        Predict_Out[i,1,1] = average_precision_score(y_test[:,1], dis_predict[:,1])
        Predict_Out[i,2,1] = accuracy_score(y_test[:,1], 1. * (dis_predict[:,1]>0.5) )
            
    # Mean / Var of 20 different testing sets
    Output = np.round(np.concatenate((np.mean(Predict_Out,0),np.std(Predict_Out,0)),axis = 1),4) 
    
    print(Output)
