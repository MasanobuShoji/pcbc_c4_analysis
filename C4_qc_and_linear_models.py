# -*- coding: utf-8 -*-
# <nbformat>3.0</nbformat>

# <codecell>

import synapseclient
import IPython.display as display
from IPython.core.display import display as display2
import numpy as np
import pandas as pd
import scipy.stats as stats
import statsmodels.formula.api as sm
import MicroArray
from synapseHelpers import query2df
from toppGenePost import ToppGeneEnrichement
from multiprocessing import Pool

EXPR_ID = 'syn1968267'
METH_ID = 'syn2233188'
MIRSEQ_ID = 'syn2233189'
METADATA_ID = 'syn2248030'
pd.set_option('max_columns', 200)
syn = synapseclient.login()
pool=Pool()

# <markdowncell>

# ##Download and annotate data
# 1. Downloads expression data from Synapse
# 2. Gets annotations from metadata in Synapse
# 3. Explore correlation between metadata fields

# <markdowncell>

# Query synapse for metadata of the expression data and store in a pandas dataframe

# <codecell>

QUERY = 'select * from entity where passqc=="PASS" and diffnameshort=="SC" and parentId=="%s"' %METADATA_ID
metadata = query2df(syn.query(QUERY))
metadata.index = metadata['decoratedName']
metadata = metadata.drop(['decoratedName', 'name','passqc', 'bamId', 'fastqId','approvedfordiff', 
                          'pub', 'projdiff', 'projdifforiginlab', 'predifferentiatedcellname', 
                          'project',], axis=1)

#Replace text 'None' with None
metadata.replace(u'None', np.nan, inplace=True)

#Simplify certain disease labels
metadata.diffnameshort[metadata.diffnameshort=='EB-LF']='EB'
metadata.diffnameshort[metadata.diffnameshort=='SC-LF']='SC'
metadata.diffnameshort[metadata.diffnameshort=='SC-hpx']='SC'
#Display a table of metadata
display.HTML(metadata.to_html())#[['inductiongenes', 'donorsex', 'diffnameshort', 'donorstage', 'linetype', 'origcell']].to_html())

# <markdowncell>

# ##Quality Control - Covariates
# A quick perusal of the metadata seems to indicate that there is a large concordance between certain of the experimental and technical variables.  To explore this I will do a table test to look for experimental and technical variables that may be confounded.  For those that have high overlap we can look at the specific contigency tables. 
# 
# **note:** Even though the assumptions for performing a chi-square test instead of a Fisher exact test on each contigency table I do the former for simplicity.

# <codecell>

chi_p_vals = np.zeros((metadata.shape[1], metadata.shape[1]))
for i, label1 in enumerate(metadata.columns):
    for j, label2 in enumerate(metadata.columns):
        if label1!=label2:
            chi_p_vals[i,j] = stats.chi2_contingency(pd.crosstab(metadata[label1].dropna(), metadata[label2].dropna()))[1]
log_chi_p_vals = np.log10(chi_p_vals)

#Plot sorted data
transformedData = log_chi_p_vals
pylab.figure(figsize=(10,10))
pylab.imshow(transformedData, interpolation='nearest'); pylab.colorbar()
pylab.xticks(range(metadata.shape[1]), metadata.columns, rotation=90)
pylab.yticks(range(metadata.shape[1]), metadata.columns)
pylab.title('Similarity betweeen covariates')
print 

# <markdowncell>

# Looking at these p-values and extracting the contigency tables of the extremely significant variables below a story emerges where some of these significant associations are expected while others might be more problematic.
# 
# ####Non-problematic associations:
#   
#   *  **donorstage-linetype**, **donorstage-origcell**, and **origcell-linetype** are all significantly linked because all except one HESC are blastocyst which all stem from embryo.
#   *  **lane-run** This will make it hard to easily correct for run-lane effects but all lanes have been used in at least one run which means a lane specific model could be built if necesary.
#   * **vector-inductiongenes** means that we can't distinguish signals that are specific for vector or induction genes. Unfortunately these seem highly correlated with originlab as well (see below).
#   
# ####More problematic overlapping annotations:
# 
#   * **origlab-, -vector, -inductiongenes** Each lab predominantly uses a single set of induction genes for example the Gaude/Weis lab exlusively used OSKM
#   * **origlab-donorstage** Specific labs contribute specific samples, for example Zambidis lab is identical to neonate; Conklin is identical to adult; Gadue/Weis identical to ped
#   * **origlab-origcell** shows problems with the Zambidis UCB CD34+ samples but is evenly distributed for the fibroblasts.
#   * **run-, origcell, origlab, vector** makes it very hard to extract experimental artifacts for example 9/10 of the Zambidis samples which is also 9/10 of the UCB CD34+ samples were processed in one run.

# <codecell>

#Display the contigency tables for the lowest p-values
html = ''
for i, j in zip(*np.where(log_chi_p_vals<-8)):
    if i>j: 
        html+= '<b>%s - %s: p<%0.3g</b>' %(metadata.columns[i], metadata.columns[j], 10**log_chi_p_vals[i,j])
        html+=(pd.crosstab(metadata.ix[:,i].dropna(),metadata.ix[:,j].dropna()).to_html())
display.HTML(html)

# <markdowncell>

# ###Association between covariates and Expression Data
# By looking at the principal signals in the expression data we can extract the assocation with these with each of the metadata.  I'll start by downloading the expression and processing the expression data.

# <codecell>

#Fetch the expression data from Synapse
exprEnt =syn.get(EXPR_ID)
exprData = pd.read_csv(exprEnt.path, sep='\t', index_col=0)

#Remove samples which are not in the metadata (i.e. that failed QC or not in the where statement above)
cols = ['symbol', 'locus']; cols.extend(metadata.index)
exprData = exprData[cols]

# <markdowncell>

# Remove genes with low variance (standard deviation<.1) or where 80% samples have no expression

# <codecell>

#Remove genes with excessive non-expressed samples (i.e >80% of samples with 0 expression)
idx = (exprData==0).sum(1)/float(exprData.shape[1]-2) <=.2
#AND low variance across samples
idx &= (np.log2(exprData.ix[:,2:]+1).std(1)>.1)

exprData = exprData.ix[idx,:]
symbol = exprData['symbol']
locus = exprData['locus']
exprData = exprData.ix[:,2:]
print exprData.shape

# <markdowncell>

# Log transform and normalize data to Z-scores

# <codecell>

##Log transform data
logExprData = np.log2(exprData.ix[:,:]+1)
##normalize each sample to be z-score
logExprData = logExprData - logExprData.mean(skipna=True)
logExprData.ix[:,:] = logExprData.ix[:,:]/logExprData.ix[:,:].std(skipna=True)

# <codecell>

reload(MicroArray)
#Standardize each row (gene) to z-score
d = logExprData.T - logExprData.T.mean(skipna=True)
d.ix[:,:] = d.ix[:,:]/d.ix[:,:].std(skipna=True)
pylab.figure(figsize=(20,20))
u, s, vt = MicroArray.QaD_SVD(d.T, metadata.drop('diffnameshort', axis=1));

# <codecell>

#For sanity lets look at the top gender associated genes in toppGene
idx = np.argsort(u[:,1])
ToppGeneEnrichement(pd.concat([symbol[idx][:100], symbol[idx][-100:]]))
#for i in idx[-10:]:
#    df = pd.concat([exprData.ix[i,:], metadata['donorsex'], metadata['cnv'], metadata['ratio']], axis=1)
#    df.columns = ['gene', 'gender', 'cnv', 'ratio']
#    df.boxplot(['gene'], by=['gender'])

# <markdowncell>

# ###Observations about the expression data
# The association between the expression data and the metadata unfortunately shows that the majority of the signal in the data is associated with experimental covariates.  Specifically we are seeing strong association between copy number variation, gender run and the ration QC metric. It should be easy to correct for these using the residuals of a linear model

# <markdowncell>

# ##Normalization based on covariates
# Fit a model of expression of each gene as a function of gender and copy number variation, that is
# 
# $$Y_{gene} = \beta_0 + \beta_1\cdot X_{gender} + \beta_2\cdot X_{cnv} + Error$$ 
# 
# The residuals of this model will contain the remaining variation in the data that is independent of gender or copy number variation.
# 
# **Note** If the copy number labels are complete it would probably be better to correct for the copy number variation in those regions of the genome that are indeed duplicated instead of on a global scale.

# <codecell>

logExprDataNorm = np.empty_like(exprData.as_matrix())
for i in range(exprData.shape[0]):
    #Build up data frame of gene and covariates
    df = pd.concat([logExprData.ix[i,:], metadata['donorsex'], metadata['cnv'], metadata['ratio']], axis=1)
    df.columns = ['gene', 'gender', 'cnv', 'ratio']
    #Fit model
    mod = sm.ols('gene ~ gender + cnv', df)
    res = mod.fit()
    df['res']=res.resid
    logExprDataNorm[i,:] = df['res']
    #df.boxplot(['gene','res'], by=['gender'])
logExprDataNorm = pd.DataFrame(logExprDataNorm, columns=exprData.columns, index=exprData.index)

# <markdowncell>

# ###PCA of residuals
# Perform PCA of the normalized samples

# <codecell>

d = logExprDataNorm.dropna(axis=1)
u, s, vt = MicroArray.QaD_SVD(d, metadata.drop('diffnameshort', axis=1).ix[d.columns,:]);

# <markdowncell>

# We can explore the genes most strongly associated with these PC by looking at the loadings in ToppGene

# <codecell>

for i in range(2):
    topGeneIdx=argsort(u[:,i])
    display.display_html(ToppGeneEnrichement(symbol[topGeneIdx][:100], name='Anti-Correlated PC%i'%(i+1)))
    display.display_html(ToppGeneEnrichement(symbol[topGeneIdx][-100:],  name='Correlated PC%i'%(i+1)))

# <markdowncell>

# ##Cell of origin and Induction gene analysis

# <codecell>

%%time
#Put the data into temporary datastructure
df = logExprDataNorm.T
#I have to temporarily rename the genes to work with Patsy formulas
df.columns = ['gene_%i' %i for i in range(len(df.columns))] 
df['origcell'] = metadata['origcell']
df['inductiongenes'] = metadata['inductiongenes']

def model(geneName):
    mod1 = sm.ols('%s ~ origcell' %geneName, df)
    mod2 = sm.ols('%s ~ inductiongenes' %geneName, df)
    return mod1.fit().f_pvalue, mod2.fit().f_pvalue
    
pvals = np.asarray(pool.map(model, df.columns[:-2]))
    
models = pd.DataFrame(symbol.copy())
models['origcell_models']=pvals[:,0]
models['inductiongenes_models']=pvals[:,1]
#models['p_origcell'] = [x.f_pvalue for x in model_origcell]
#models['p_inductiongenes']=[x.f_pvalue for x in model_inductiongenes]  

