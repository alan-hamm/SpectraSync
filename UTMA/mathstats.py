import cupy as cp
import logging
from gensim.models.coherencemodel import CoherenceModel
from gensim.corpora.dictionary import Dictionary
from gensim.models import LdaModel
import numpy as np
import math
from decimal import Decimal, InvalidOperation

# Sample initial documents for coherence calculation
def sample_coherence(ldamodel, documents, dictionary, sample_ratio=0.1):
    sample_size = int(len(documents) * sample_ratio)
    sample_indices = np.random.choice(len(documents), sample_size, replace=False)
    sample_docs = [documents[i] for i in sample_indices]
    coherence_model = CoherenceModel(model=ldamodel, texts=sample_docs, dictionary=dictionary, coherence='c_v')
    return coherence_model.get_coherence_per_topic()

# Calculate mean, median, and mode using CuPy for GPU acceleration
def calculate_statistics(coherence_scores):
    coherence_array = cp.array(coherence_scores)
    mean_coherence = cp.mean(coherence_array).item()
    median_coherence = cp.median(coherence_array).item()
    mode_coherence = cp.argmax(cp.bincount(coherence_array.astype(int))).item()
    return mean_coherence, median_coherence, mode_coherence

# Wrapper to sample coherence scores for a specific training phase
#  -    The function sample_coherence_for_phase is a wrapper designed to call another 
#       function named sample_coherence, which is not defined in the provided code snippet. In typical setups, sample_coherence would be implemented to:
#
#   -   Randomly sample a subset of documents based on sample_ratio.
#   -   Compute coherence scores for the sampled documents using the LDA model.
#   -   The source of the sample would be the list of documents passed to sample_coherence_for_phase, with sample_ratio determining the proportion of
#        documents to use in coherence calculation. If sample_coherence is defined elsewhere, it would be expected to look something like this:

    # Sampling: random.sample takes a subset of documents based on sample_ratio.
    # Coherence Calculation: Uses only the sampled documents to calculate the coherence score.
    # You would call sample_coherence_for_phase, which in turn calls sample_coherence to perform the sampling and 
    # coherence scoring. The sample_ratio allows control over the sample size, helping reduce computation if only a small 
    # subset is needed for an approximate coherence score.
def sample_coherence_for_phase(ldamodel: LdaModel, documents: list, dictionary: Dictionary, sample_ratio: float):
    return sample_coherence(ldamodel, documents, dictionary, sample_ratio)

# Wrapper to get coherence statistics using GPU
def get_statistics(coherence_scores):
    # Convert coherence scores to a CuPy array
    coherence_array = cp.array(coherence_scores)
    
    # Filter out non-finite values (e.g., NaN, inf) from the array
    coherence_array = coherence_array[cp.isfinite(coherence_array)]
    
    if coherence_array.size == 0:
        # Return NaN if all values are invalid
        return float('nan'), float('nan'),float('nan'),float('nan')
    
    # Calculate statistics using CuPy
    mean_coherence = cp.mean(coherence_array).item()
    median_coherence = cp.median(coherence_array).item()
    std_coherence = cp.std(coherence_array).item()
    
    try:
        mode_coherence = cp.argmax(cp.bincount(coherence_array.astype(int))).item()
    except ValueError:
        mode_coherence = float('nan')  # Fallback if bincount fails
    
    return mean_coherence, median_coherence, mode_coherence, std_coherence


# Calculate perplexity-based threshold
def calculate_perplexity_threshold(ldamodel: LdaModel, documents):
    perplexity = ldamodel.log_perplexity(documents)
    threshold = 0.8 * perplexity  # Adjust this multiplier based on observed correlation
    return threshold

# Main decision logic based on coherence statistics and threshold
def coherence_score_decision(ldamodel, documents, dictionary, initial_sample_ratio=0.1):
    # Sample coherence scores
    coherence_scores = sample_coherence_for_phase(ldamodel, documents, dictionary, initial_sample_ratio)
    
    # Calculate coherence statistics
    mean_coherence, median_coherence, mode_coherence, std_coherence = get_statistics(coherence_scores)
    
    # Calculate perplexity threshold
    try:
        threshold = calculate_perplexity_threshold(ldamodel, documents, dictionary)
    except Exception as e:
        logging.error(f"Error calculating perplexity threshold: {e}")
        threshold = float('nan')
    
    # Check coherence threshold
    coherence_score = mean_coherence if mean_coherence < threshold else float('nan')
    
    # Ensure all values are returned
    return (coherence_score, mean_coherence, median_coherence, mode_coherence, std_coherence, threshold)

# Function to replace NaN with interpolated values
def replace_nan_with_interpolated(data):
    # Calculate mean values for replacement where possible
    if math.isnan(data.get('mean_coherence', float('nan'))):
        data['mean_coherence'] = sum(data.get('coherence_scores', [])) / max(len(data.get('coherence_scores', [])), 1)
        
    if math.isnan(data.get('median_coherence', float('nan'))):
        coherence_scores = sorted(data.get('coherence_scores', []))
        mid = len(coherence_scores) // 2
        data['median_coherence'] = (coherence_scores[mid] + coherence_scores[~mid]) / 2 if coherence_scores else data['mean_coherence']
        
    if math.isnan(data.get('std_coherence', float('nan'))):
        if len(data.get('coherence_scores', [])) > 1:
            mean = data['mean_coherence']
            std_dev = (sum((x - mean) ** 2 for x in data['coherence_scores']) / (len(data['coherence_scores']) - 1)) ** 0.5
            data['std_coherence'] = std_dev
        else:
            data['std_coherence'] = 0  # Standard deviation of a single value is 0
        
    return data

# Function to handle NaN replacement with high precision using CuPy
def replace_nan_with_high_precision(default_score, data):
    # Replace `NaN` values in model data with calculated or meaningful values
    for key, value in data.items():
        if isinstance(value, float) and math.isnan(value):
            try:
                coherence_scores = data.get('coherence_scores', [])
                
                if coherence_scores:
                    # Convert coherence scores to CuPy array for GPU processing
                    coherence_scores_cp = cp.array(coherence_scores, dtype=cp.float64)

                    if key == 'mean_coherence':
                        # Calculate mean coherence using CuPy
                        mean_coherence = cp.mean(coherence_scores_cp).item()
                        data[key] = mean_coherence
                    
                    elif key == 'median_coherence':
                        # Calculate median coherence using CuPy
                        coherence_scores_cp.sort()
                        mid = len(coherence_scores_cp) // 2
                        if len(coherence_scores_cp) % 2 == 0:
                            median_coherence = (coherence_scores_cp[mid - 1] + coherence_scores_cp[mid]) / 2
                        else:
                            median_coherence = coherence_scores_cp[mid]
                        data[key] = median_coherence.item()
                    
                    elif key == 'std_coherence':
                        # Calculate standard deviation coherence using CuPy
                        std_coherence = cp.std(coherence_scores_cp, ddof=1).item()
                        data[key] = std_coherence
                        
                    else:
                        data[key] = default_score  # Use a default for other keys if needed

                else:
                    data[key] = default_score  # Use default if coherence_scores is empty

            except InvalidOperation:
                data[key] = default_score  # Fallback if calculations fail

    return data