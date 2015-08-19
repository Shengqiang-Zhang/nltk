# -*- coding: utf-8 -*-
# Natural Language Toolkit: IBM Model Core
#
# Copyright (C) 2001-2015 NLTK Project
# Author: Tah Wei Hoon <hoon.tw@gmail.com>
# URL: <http://nltk.org/>
# For license information, see LICENSE.TXT

"""
Common methods and classes for all IBM models. See ``IBMModel1``,
``IBMModel2``, and ``IBMModel3`` for specific implementations.

The IBM models are a series of generative models that learn lexical
translation probabilities, p(target language word|source language word),
given a sentence-aligned parallel corpus.

The models increase in sophistication from model 1 to 5. Typically, the
output of lower models is used to seed the higher models. All models
use the Expectation-Maximization (EM) algorithm to learn various
probability tables.

Words in a sentence are one-indexed. The first word of a sentence has
position 1, not 0. Index 0 is reserved in the source sentence for the
NULL token. The concept of position does not apply to NULL, but it is
indexed at 0 by convention.

Each target word is aligned to exactly one source word or the NULL
token.

References:
Philipp Koehn. 2010. Statistical Machine Translation.
Cambridge University Press, New York.

Peter E Brown, Stephen A. Della Pietra, Vincent J. Della Pietra, and
Robert L. Mercer. 1993. The Mathematics of Statistical Machine
Translation: Parameter Estimation. Computational Linguistics, 19 (2),
263-311.
"""

from collections import defaultdict


class IBMModel(object):
    """
    Abstract base class for all IBM models
    """

    # Avoid division by zero and precision errors by imposing a minimum
    # value for probabilities. Note that this approach is theoretically
    # incorrect, since it may create probabilities that sum to more
    # than 1. In practice, the contribution of probabilities with MIN_PROB
    # is tiny enough that the value of MIN_PROB can be treated as zero.
    MIN_PROB = 1.0e-12 # GIZA++ is more liberal and uses 1.0e-7

    def __init__(self, sentence_aligned_corpus):
        self.init_vocab(sentence_aligned_corpus)

        self.translation_table = defaultdict(
            lambda: defaultdict(lambda: IBMModel.MIN_PROB))
        """
        dict[str][str]: float. Probability(target word | source word).
        Values accessed as ``translation_table[target_word][source_word]``.
        """

        self.alignment_table = defaultdict(
            lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(
                lambda: IBMModel.MIN_PROB))))
        """
        dict[int][int][int][int]: float. Probability(i | j,l,m).
        Values accessed as ``alignment_table[i][j][l][m]``.
        Used in model 2 and hill climbing in models 3 and above
        """

        self.fertility_table = defaultdict(
            lambda: defaultdict(lambda: self.MIN_PROB))
        """
        dict[int][str]: float. Probability(fertility | source word).
        Values accessed as ``fertility_table[fertility][source_word]``.
        Used in model 3 and higher.
        """

        # Initial probability of null insertion
        self.p1 = 0.5
        """
        Probability that a generated word requires another target word
        that is aligned to NULL.
        Used in model 3 and higher.
        """

    def init_vocab(self, sentence_aligned_corpus):
        src_vocab = set()
        trg_vocab = set()
        for aligned_sentence in sentence_aligned_corpus:
            trg_vocab.update(aligned_sentence.words)
            src_vocab.update(aligned_sentence.mots)
        # Add the NULL token
        src_vocab.add(None)

        self.src_vocab = src_vocab
        """
        set(str): All source language words used in training
        """

        self.trg_vocab = trg_vocab
        """
        set(str): All target language words used in training
        """

    def sample(self, trg_sentence, src_sentence):
        """
        Sample the most probable alignments from the entire alignment
        space

        First, peg one alignment point and determine the best alignment
        according to IBM Model 2. With this initial alignment, use hill
        climbing to determine the best alignment according to Model 3.
        This alignment and its neighbors are added to the sample set.
        This process is repeated by pegging different alignment points.

        Hill climbing may be stuck in a local maxima, hence the pegging
        and trying out of different alignments.

        :return: A set of best alignments represented by their ``AlignmentInfo``
        :rtype: set(AlignmentInfo)
        """

        sampled_alignments = set()

        l = len(src_sentence) - 1 # exclude NULL
        m = len(trg_sentence)

        for i in range(0, l + 1):
            for j in range(1, m + 1):
                alignment = [0] * (m + 1) # Initialize all alignments to NULL
                fertility_of_i = [0] * (l + 1)

                # Pegging one alignment point
                alignment[j] = i
                fertility_of_i[i] = 1

                for jj in range(1, m + 1):
                    if jj != j:
                        # Find the best alignment according to model 2
                        max_alignment_prob = IBMModel.MIN_PROB
                        best_i = 1

                        for ii in range(0, l + 1):
                            s = src_sentence[ii]
                            t = trg_sentence[jj - 1]
                            alignment_prob = (self.translation_table[t][s] *
                                self.alignment_table[ii][jj][l][m])
                            if alignment_prob > max_alignment_prob:
                                max_alignment_prob = alignment_prob
                                best_i = ii

                        alignment[jj] = best_i
                        fertility_of_i[best_i] += 1

                alignment_info = AlignmentInfo(
                    tuple(alignment), tuple(src_sentence),
                    tuple(trg_sentence), tuple(fertility_of_i))
                best_alignment = self.hillclimb(alignment_info, j)
                neighbors = self.neighboring(best_alignment, j)
                sampled_alignments.update(neighbors)

        return sampled_alignments

    def hillclimb(self, alignment_info, j_pegged):
        """
        Starting from the alignment in ``alignment_info``, look at
        neighboring alignments iteratively for the best one

        There is no guarantee that the best alignment in the alignment
        space will be found, because the algorithm might be stuck in a
        local maximum.

        :return: The best alignment found from hill climbing
        :rtype: AlignmentInfo
        """

        alignment = alignment_info # alias with shorter name
        while True:
            old_alignment = alignment

            for neighbor_alignment in self.neighboring(alignment, j_pegged):
                neighbor_probability = self.probability(neighbor_alignment)
                current_probability = self.probability(alignment)

                if neighbor_probability > current_probability:
                    alignment = neighbor_alignment

            if alignment == old_alignment:
                # Until there are no better alignments
                break

        return alignment_info

    def neighboring(self, alignment_info, j_pegged):
        """
        Determine the neighbors of ``alignment_info``, obtained by
        moving or swapping one alignment point

        :return: A set neighboring alignments represented by their
            ``AlignmentInfo``
        :rtype: set(AlignmentInfo)
        """

        neighbors = set()

        l = len(alignment_info.src_sentence) - 1 # exclude NULL
        m = len(alignment_info.trg_sentence)
        original_alignment = alignment_info.alignment
        original_fertility = alignment_info.fertility_of_i

        for j in range(1, m + 1):
            if j != j_pegged:
                # Add alignments that differ by one alignment point
                for i in range(0, l + 1):
                    new_alignment = list(original_alignment)
                    new_fertility = list(original_fertility)

                    new_alignment[j] = i
                    new_fertility[i] += 1
                    new_fertility[original_alignment[j]] -= 1

                    new_alignment_info = AlignmentInfo(
                        tuple(new_alignment), alignment_info.src_sentence,
                        alignment_info.trg_sentence, tuple(new_fertility))
                    neighbors.add(new_alignment_info)

        for j in range(1, m + 1):
            if j != j_pegged:
                # Add alignments that have two alignment points swapped
                for other_j in range(1, m + 1):
                    if other_j != j_pegged and other_j != j:
                        new_alignment = list(original_alignment)
                        new_fertility = list(original_fertility)
                        new_alignment[j] = original_alignment[other_j]
                        new_alignment[other_j] = original_alignment[j]

                        new_alignment_info = AlignmentInfo(
                            tuple(new_alignment), alignment_info.src_sentence,
                            alignment_info.trg_sentence, tuple(new_fertility))
                        neighbors.add(new_alignment_info)

        return neighbors



class AlignmentInfo(object):
    """
    Helper data object for training IBM Model 3

    Read-only. For a source sentence and its counterpart in the target
    language, this class holds information about the sentence pair's
    alignment and fertility.
    """

    def __init__(self, alignment, src_sentence, trg_sentence, fertility_of_i):
        if not isinstance(alignment, tuple):
            raise TypeError("The alignment must be a tuple because it is used "
                            "to uniquely identify AlignmentInfo objects.")

        self.alignment = alignment
        """
        tuple(int): Alignment function. ``alignment[j]`` is the position
        in the source sentence that is aligned to the position j in the
        target sentence.
        """

        self.fertility_of_i = fertility_of_i
        """
        tuple(int): Fertility of source word. ``fertility_of_i[i]`` is
        the number of words in the target sentence that is aligned to
        the word in position i of the source sentence.
        """

        self.src_sentence = src_sentence
        """
        tuple(str): Source sentence referred to by this object.
        Should include NULL token (None) in index 0.
        """

        self.trg_sentence = trg_sentence
        """
        tuple(str): Target sentence referred to by this object.
        """

    def __eq__(self, other):
        return self.alignment == other.alignment

    def __hash__(self):
        return hash(self.alignment)
