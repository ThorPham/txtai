"""
Word Vectors module
"""

import os
import pickle
import tempfile

from errno import ENOENT
from multiprocessing import Pool

import fasttext
import numpy as np

from pymagnitude import converter, Magnitude

from .base import Vectors
from ..pipeline.tokenizer import Tokenizer

# Multiprocessing helper methods
# pylint: disable=W0603
VECTORS = None


def create(config, scoring):
    """
    Multiprocessing helper method. Creates a global embeddings object to be accessed in a new subprocess.

    Args:
        config: vector configuration
        scoring: scoring instance
    """

    global VECTORS

    # Create a global embedding object using configuration and saved
    VECTORS = WordVectors(config, scoring)


def transform(document):
    """
    Multiprocessing helper method. Transforms document into an embeddings vector.

    Args:
        document: (id, text|tokens, tags)

    Returns:
        (id, embedding)
    """

    global VECTORS

    return (document[0], VECTORS.transform(document))


class SerialPool:
    """
    Custom pool to execute vector transforms serially.
    """

    def __init__(self, vectors):
        global VECTORS
        VECTORS = vectors

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def imap(self, func, iterable):
        """
        Single process version of imap.

        Args:
            func: function to run
            iterable: iterable to use
        """

        for x in iterable:
            yield func(x)


class WordVectors(Vectors):
    """
    Builds sentence embeddings/vectors using weighted word embeddings.
    """

    def load(self, path):
        # Ensure that vector path exists
        if not path or not os.path.isfile(path):
            raise IOError(ENOENT, "Vector model file not found", path)

        # Load magnitude model. If this is a training run (uninitialized config), block until vectors are fully loaded
        return Magnitude(path, case_insensitive=True, blocking=not self.initialized)

    def index(self, documents):
        ids, dimensions, stream = [], None, None

        # Shared objects with Pool
        args = (self.config, self.scoring)

        # Convert all documents to embedding arrays, stream embeddings to disk to control memory usage
        with SerialPool(self) if "parallel" in self.config and not self.config["parallel"] else Pool(
            os.cpu_count(), initializer=create, initargs=args
        ) as pool:
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".npy", delete=False) as output:
                stream = output.name
                for uid, embedding in pool.imap(transform, documents):
                    if not dimensions:
                        # Set number of dimensions for embeddings
                        dimensions = embedding.shape[0]

                    ids.append(uid)
                    pickle.dump(embedding, output)

        return (ids, dimensions, stream)

    def transform(self, document):
        # Convert to tokens if necessary
        if isinstance(document[1], str):
            document = (document[0], Tokenizer.tokenize(document[1]), document[2])

        # Generate weights for each vector using a scoring method
        weights = self.scoring.weights(document) if self.scoring else None

        # pylint: disable=E1133
        if weights and [x for x in weights if x > 0]:
            # Build weighted average embeddings vector. Create weights array os float32 to match embeddings precision.
            embedding = np.average(self.lookup(document[1]), weights=np.array(weights, dtype=np.float32), axis=0)
        else:
            # If no weights, use mean
            embedding = np.mean(self.lookup(document[1]), axis=0)

        return embedding

    def lookup(self, tokens):
        """
        Queries word vectors for given list of input tokens.

        Args:
            tokens: list of tokens to query

        Returns:
            word vectors array
        """

        return self.model.query(tokens)

    @staticmethod
    def build(data, size, mincount, path):
        """
        Builds fastText vectors from a file.

        Args:
            data: path to input data file
            size: number of vector dimensions
            mincount: minimum number of occurrences required to register a token
            path: path to output file
        """

        # Train on data file using largest dimension size
        model = fasttext.train_unsupervised(data, dim=size, minCount=mincount)

        # Output file path
        print("Building %d dimension model" % size)

        # Output vectors in vec/txt format
        with open(path + ".txt", "w") as output:
            words = model.get_words()
            output.write("%d %d\n" % (len(words), model.get_dimension()))

            for word in words:
                # Skip end of line token
                if word != "</s>":
                    vector = model.get_word_vector(word)
                    data = ""
                    for v in vector:
                        data += " " + str(v)

                    output.write(word + data + "\n")

        # Build magnitude vectors database
        print("Converting vectors to magnitude format")
        converter.convert(path + ".txt", path + ".magnitude", subword=True)
