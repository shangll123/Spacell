from tensorflow.keras.layers import Dense, GlobalAveragePooling2D, Input, concatenate, Dropout, Lambda
from tensorflow.keras.applications.inception_v3 import InceptionV3, preprocess_input as preprocess_inception
from tensorflow.keras.applications.xception import Xception, preprocess_input as preprocess_xception
from tensorflow.keras.losses import mse, binary_crossentropy
from tensorflow.keras.models import Model, Sequential
from tensorflow.keras.optimizers import Adam
import os
from tensorflow.keras.utils import multi_gpu_model
from tensorflow.keras.applications.xception import Xception
from sklearn.metrics import confusion_matrix, roc_curve, auc
import seaborn as sns
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tensorflow.keras.applications.resnet50 import ResNet50, preprocess_input as preprocess_resnet, decode_predictions
from tensorflow.keras import backend as K


class ResNet:
    __name__ = "ResNet"

    def __init__(self, batch_size=1):
        self.model = ResNet50(include_top=False, weights='imagenet', pooling="avg")
        self.batch_size = batch_size
        self.data_format = K.image_data_format()

    def predict(self, x):
        if self.data_format == "channels_first":
            x = x.transpose(0, 3, 1, 2)
        x = preprocess_resnet(x.astype(K.floatx()))
        return self.model.predict(x, batch_size=self.batch_size)


class Inception_V3:
    """
    pre-trained Inception_V3 model
    """
    __name__ = "Inception_V3"

    def __init__(self, batch_size=1):
        self.model = InceptionV3(include_top=False, weights='imagenet', pooling="avg")
        self.batch_size = batch_size
        self.data_format = K.image_data_format()

    def predict(self, x):
        if self.data_format == "channels_first":
            x = x.transpose(0, 3, 1, 2)
        x = preprocess_inception(x.astype(K.floatx()))
        return self.model.predict(x, batch_size=self.batch_size)


class Xception_imagenet:
    """
    pre-trained xception model
    """
    __name__ = "xception"

    def __init__(self, batch_size=1):
        self.model = Xception(include_top=False, weights='imagenet', pooling="avg")
        self.batch_size = batch_size
        self.data_format = K.image_data_format()

    def predict(self, x):
        if self.data_format == "channels_first":
            x = x.transpose(0, 3, 1, 2)
        x = preprocess_xception(x.astype(K.floatx()))
        return self.model.predict(x, batch_size=self.batch_size)


def encode(tiles, model):
    features = model.predict(tiles)
    features = features.ravel()
    return features


def features_gen(tile_and_infor, model, out_path):
    current_file = None
    df = pd.DataFrame()
    for j, (tile, output_infor) in enumerate(tile_and_infor):
        print("generate features for {}th tile".format(j+1))
        spot = output_infor[1] + 'x' + output_infor[2]
        if current_file is not None:
            assert current_file == output_infor[0]
        current_file = output_infor[0]
        features = encode(tile, model)
        df[spot] = features
    out_path = os.path.join(out_path, current_file)
    assert len(df) > 0
    df.to_csv(out_path + '.tsv', header=True, index=False, sep="\t")


def autoencoder(n_input, loss='mean_squared_error'):
    '''
    model.fit(x_train, x_train, batch_size = 32, epochs = 500)
    bottleneck_representation = encoder.predict(x_train)
    '''
    model = Sequential()
    # encoder
    model.add(Dense(512,       activation='relu', input_shape=(n_input,)))
    model.add(Dense(256,       activation='relu'))
    model.add(Dense(64,        activation='relu'))
    # bottleneck code
    model.add(Dense(20,         activation='linear', name="bottleneck"))
    # decoder
    model.add(Dense(64,        activation='relu'))
    model.add(Dense(256,       activation='relu'))
    model.add(Dense(512,       activation='relu'))
    model.add(Dense(n_input,   activation='sigmoid'))
    model.compile(loss=loss, optimizer=Adam())
    encoder = Model(model.input, model.get_layer('bottleneck').output)
    return model, encoder


def sampling(args):
    z_mean, z_log_var = args
    batch = K.shape(z_mean)[0]
    dim = K.int_shape(z_mean)[1]
    epsilon = K.random_normal(shape=(batch, dim))
    return z_mean + K.exp(0.5 * z_log_var) * epsilon


def vae(original_dim, intermediate_dim=512, latent_dim=20):
    '''
    vae.fit(x_train, epochs=epochs, batch_size=batch_size, validation_data=(x_test, None))
    bottleneck_representation,_,_ = encoder.predict(x_test)
    '''
    # encoder
    input_shape = (original_dim,)
    inputs = Input(shape=input_shape, name='encoder_input')
    x = Dense(intermediate_dim, activation='relu')(inputs)
    z_mean = Dense(latent_dim, name='z_mean')(x)
    z_log_var = Dense(latent_dim, name='z_log_var')(x)

    z = Lambda(sampling, output_shape=(latent_dim,), name='z')([z_mean, z_log_var])

    encoder = Model(inputs, [z_mean, z_log_var, z], name='encoder')
    # encoder.summary()
    # plot_model(encoder, to_file='vae_mlp_encoder.png', show_shapes=True)

    # decoder
    latent_inputs = Input(shape=(latent_dim,), name='z_sampling')
    x = Dense(intermediate_dim, activation='relu')(latent_inputs)
    outputs = Dense(original_dim, activation='sigmoid')(x)

    decoder = Model(latent_inputs, outputs, name='decoder')
    # decoder.summary()
    # plot_model(decoder, to_file='vae_mlp_decoder.png', show_shapes=True)

    # VAE model
    outputs = decoder(encoder(inputs)[2])
    vae = Model(inputs, outputs, name='vae')

    # loss
    reconstruction_loss = binary_crossentropy(inputs, outputs)
    reconstruction_loss *= original_dim
    kl_loss = 1 + z_log_var - K.square(z_mean) - K.exp(z_log_var)
    kl_loss = K.sum(kl_loss, axis=-1)
    kl_loss *= -0.5
    vae_loss = K.mean(reconstruction_loss + kl_loss)
    vae.add_loss(vae_loss)
    vae.compile(optimizer='adam')
    # vae.summary()
    # plot_model(vae, to_file='vae_mlp.png', show_shapes=True)

    return vae, encoder


def combine_ae(ge_dim, tfv_dim, loss='mean_squared_error'):
    '''
    combine_ae.fit([X_ge, X_tfv],
                   [X_ge, X_tfv],
                    epochs = 100, batch_size = 128,
                    validation_split = 0.2, shuffle = True)
    bottleneck_representation = encoder.predict([X_ge, X_tfv])
    '''
    # Input Layer
    input_dim_ge = Input(shape=(ge_dim,), name="gene_expression")
    input_dim_tfv = Input(shape=(tfv_dim,), name="tile_feature_vector")

    # Dimensions of Encoder layer
    encoding_dim_ge = 256
    encoding_dim_tfv = 256

    # Encoder layer for each dataset
    encoded_ge = Dense(encoding_dim_ge, activation='relu',
                       name="Encoder_ge")(input_dim_ge)
    encoded_tfv = Dense(encoding_dim_tfv, activation='relu',
                        name="Encoder_tfv")(input_dim_tfv)

    # Merging Encoder layers from different dataset
    merge = concatenate([encoded_ge, encoded_tfv])

    # Bottleneck compression
    bottleneck = Dense(20, kernel_initializer='uniform', activation='linear',
                       name="Bottleneck")(merge)

    # Inverse merging
    merge_inverse = Dense(encoding_dim_ge + encoding_dim_tfv,
                          activation='relu', name="Concatenate_Inverse")(bottleneck)

    # Decoder layer for each dataset
    decoded_ge = Dense(ge_dim, activation='sigmoid',
                       name="Decoder_ge")(merge_inverse)
    decoded_tfv = Dense(tfv_dim, activation='sigmoid',
                        name="Decoder_tfv")(merge_inverse)

    # Combining Encoder and Decoder into an Autoencoder model
    autoencoder = Model(inputs=[input_dim_ge, input_dim_tfv], outputs=[decoded_ge, decoded_tfv])
    encoder = Model(inputs=[input_dim_ge, input_dim_tfv], outputs=bottleneck)

    # Compile Autoencoder
    autoencoder.compile(optimizer='adam',
                        loss={'Decoder_ge': loss,
                              'Decoder_tfv': loss})
    return autoencoder, encoder



def st_comb_nn(tile_shape, cm_shape, output_shape):
    #### xception base for tile
    tile_input = Input(shape=tile_shape, name = "tile_input")
    resnet_base = ResNet50(input_tensor=tile_input, weights='imagenet', include_top=False)
    stage_5_start = resnet_base.get_layer("res5a_branch2a")
    for i in range(resnet_base.layers.index(stage_5_start)):
        resnet_base.layers[i].trainable = False

    x_tile = resnet_base.output
    x_tile = GlobalAveragePooling2D()(x_tile)
    x_tile = Dropout(0.5)(x_tile)
    x_tile = Dense(512, activation='relu', name="tile_fc")(x_tile)
    #### NN for count matrix
    cm_input = Input(shape=cm_shape, name="count_matrix_input")
    x_cm = Dropout(0.5)(x_tile)
    x_cm = Dense(512, activation='relu', name="cm_fc")(x_cm)
    #### merge
    merge = concatenate([x_tile, x_cm], name="merge_tile_cm")
    merge = Dropout(0.5)(merge)
    merge = Dense(512, activation='relu', name="merge_fc_1")(merge)
    merge = Dropout(0.5)(merge)
    merge = Dense(256, activation='relu', name="merge_fc_2")(merge)
    merge = Dropout(0.5)(merge)
    merge = Dense(128, activation='relu', name="merge_fc_3")(merge)
    merge = Dropout(0.5)(merge)
    preds = Dense(output_shape, activation='softmax')(merge)
    ##### compile model
    model = Model(inputs=[tile_input, cm_input], outputs=preds)
    try:
        parallel_model = multi_gpu_model(model, gpus=4, cpu_merge=False)
    except:
        parallel_model = model
    parallel_model.compile(optimizer='Adam', loss='categorical_crossentropy', metrics=['accuracy'])
    return parallel_model, model


def model_eval(y_pred, y_true, class_list, prefix=""):
    y_true_onehot = np.zeros((len(y_true), len(class_list)))
    y_true_onehot[np.arange(len(y_true)), y_true] = 1
    y_pred_int = np.argmax(y_pred, axis=1)
    cm = confusion_matrix(y_true, y_pred_int)
    plt.figure()
    color = ['blue', 'green', 'red', 'cyan']
    for i in range(len(class_list)):
        fpr, tpr, thresholds = roc_curve(y_true_onehot[:,i], y_pred[:,i])
        roc_auc = auc(fpr, tpr)
        plt.plot(fpr, tpr, color=color[i], lw=2, label='ROC %s curve (area = %0.2f)' % (class_list[i], roc_auc))
    plt.plot([0, 1], [0, 1], color='navy', lw=2, linestyle='--')
    plt.xlim([0.0, 1.0])
    plt.ylim([0.0, 1.05])
    plt.xlabel('False Positive Rate')
    plt.ylabel('True Positive Rate')
    plt.title('roc_auc')
    plt.legend(loc="lower right")
    plt.savefig('{}_roc.pdf'.format(prefix))
    cm_plot = plot_confusion_matrix(cm, classes = class_list, prefix=prefix)


def plot_confusion_matrix(cm, classes=None, prefix=""):
    #Normalise Confusion Matrix by dividing each value by the sum of that row
    cm = cm.astype('float')/cm.sum(axis = 1)[:, np.newaxis]
    #Make DataFrame from Confusion Matrix and classes
    cm_df = pd.DataFrame(cm, index = classes, columns = classes)
    #Display Confusion Matrix 
    plt.figure(figsize = (4,4), dpi = 300)
    cm_plot = sns.heatmap(cm_df, vmin = 0, vmax = 1, annot = True, fmt = '.2f', cmap = 'Blues', square = True)
    plt.title('Confusion Matrix', fontsize = 12)
    #Display axes labels
    plt.ylabel('True label', fontsize = 12)
    plt.xlabel('Predicted label', fontsize = 12)
    plt.savefig('{}_confusion_matrix.pdf'.format(prefix))
    plt.tight_layout()
    return cm_plot


def st_nn(input_shape, output_shape):
    cm_input = Input(shape=(input_shape,), name="cm_input")
    x_cm = Dropout(0.5)(cm_input)
    # x_cm = BatchNormalization()(cm_input)
    x_cm = Dense(512, activation='relu')(cm_input)  # kernel_regularizer=regularizers.l2(0.01)
    x_cm = Dropout(0.5)(x_cm)
    # x_cm = BatchNormalization()(x_cm)
    x_cm = Dense(256, activation='relu', )(x_cm)
    x_cm = Dropout(0.5)(x_cm)
    # x_cm = BatchNormalization()(x_cm)
    x_cm = Dense(128, activation='relu')(x_cm)
    x_cm = Dropout(0.5)(x_cm)
    preds = Dense(output_shape, activation='softmax')(x_cm)
    ##### compile model
    model = Model(inputs=cm_input, outputs=preds)
    try:
        parallel_model = multi_gpu_model(model, gpus=2, cpu_merge=False)
        print("Training using multiple GPUs..")
    except ValueError:
        parallel_model = model
        print("Training using single GPU or CPU..")
    parallel_model.compile(optimizer='Adam', loss='categorical_crossentropy', metrics=['accuracy'])
    return parallel_model


def st_cnn(tile_shape, output_shape):
    tile_input = Input(shape=tile_shape, name="tile_input")
    resnet_base = ResNet50(input_tensor=tile_input, weights='imagenet', include_top=False)
    stage_5_start = resnet_base.get_layer("res5a_branch2a")
    for i in range(resnet_base.layers.index(stage_5_start)):
        resnet_base.layers[i].trainable = False

    x_tile = resnet_base.output
    x_tile = GlobalAveragePooling2D()(x_tile)
    x_tile = Dropout(0.5)(x_tile)
    x_tile = Dense(512, activation='relu', name="tile_fc_1")(x_tile)
    x_tile = Dropout(0.5)(x_tile)
    x_tile = Dense(256, activation='relu', name="tile_fc_2")(x_tile)
    x_tile = Dropout(0.5)(x_tile)
    x_tile = Dense(128, activation='relu', name="tile_fc_3")(x_tile)
    x_tile = Dropout(0.5)(x_tile)
    preds = Dense(output_shape, activation='softmax')(x_tile)
    ##### compile model
    model = Model(inputs=tile_input, outputs=preds)
    try:
        parallel_model = multi_gpu_model(model, gpus=2, cpu_relocation=True)
        print("Training using multiple GPUs..")
    except ValueError:
        parallel_model = model
        print("Training using single GPU or CPU..")
    parallel_model.compile(optimizer='Adam', loss='categorical_crossentropy', metrics=['accuracy'])
    return parallel_model


