
import pygbif
import itertools as it
import random
import pescador
import mimetypes
import requests
import hashlib
import logging
import numpy as np

from ..io import MediaData

from typing import Dict, Optional, Union, List

log = logging.getLogger(__name__)

def gbif_query_generator(
    page_limit: int = 300,
    mediatype: str = 'StillImage',
    label: str = 'speciesKey',
    *args, **kwargs
) -> MediaData:
    """Performs media queries GBIF yielding url and label

    Args:
        page_limit (int, optional): GBIF api uses paging which can be modified. Defaults to 300.
        mediatype (str, optional): Sets GBIF mediatype. Defaults to 'StillImage'.
        label (str, optional): Sets label. Defaults to 'speciesKey'.

    Returns:
        str: [description]

    Yields:
        Iterator[str]: [description]
    """
    offset = 0

    while True:
        resp = pygbif.occurrences.search(
            mediatype=mediatype,
            offset=offset,
            limit=page_limit,
            *args, **kwargs
        )
        if resp['endOfRecords']:
            break
        else:
            offset = resp['offset'] + page_limit

        # Iterate over request pages. Can possibly also done async
        for metadata in resp['results']:
            # check if media key is present
            if metadata['media']:
                # multiple media can be attached
                # select random url
                media = random.choice(metadata['media'])
                # in case the media format is not determined,
                # we need to make another request to the webserver
                # to get the content-type header
                if media['format'] is None:
                    h = requests.head(media['url'])
                    header = h.headers
                    content_type = header.get('content-type')
                else:
                    content_type = media['format']

                # hash the url, which later becomes the datatype
                hashed_url = hashlib.sha1(
                    media['identifier'].encode('utf-8')
                ).hexdigest()

                yield {
                    "url": media['identifier'],
                    "basename": hashed_url,
                    "label": str(metadata.get(label)),
                    "content_type": content_type,
                    "suffix": mimetypes.guess_extension(str(content_type)),
                }


def gbif_count(
    mediatype: str = 'StillImage',
    *args, **kwargs
) -> str:
    """Count the number of occurances from given query

    Args:
        mediatype (str, optional): [description]. Defaults to 'StillImage'.

    Returns:
        str: [description]
    """

    return pygbif.occurrences.search(
        limit=0,
        mediatype=mediatype,
        *args, **kwargs
    )['count']


def dproduct(dicts):
    """Returns the products of dicts
    """
    return (dict(zip(dicts, x)) for x in it.product(*dicts.values()))


def generate_urls(
    queries: Dict,
    label: str = "speciesKey",
    split_streams_by: Optional[Union[str, List]] = None,
    nb_samples_per_stream: Optional[int] = None,
    nb_samples: Optional[int] = None,
    weighted_streams: bool = False,
    cache_requests: bool = False,
    mediatype: str = "StillImage"
):
    """Provides url generator from given query

    Args:
        queries (Dict): 
            dictionary of queries supported by the GBIF api
        label (str, optional): label identfier, according to query api. 
            Defaults to "speciesKey".
        nb_samples (int):
            Limit the total number of samples retrieved from the API.
            When set to -1 and `split_streams_by` is not `None`,
            a minimum number of samples will be calculated
            from using the number of available samples per stream.
            Defaults to `None` which retrieves all samples from all streams until
            all streams are exchausted.
        nb_samples_per_stream (int):
            Limit the maximum number of items to be retrieved per stream.
            Defaults to `None` which retrieves all samples from stream until 
            stream generator is exhausted.
        split_streams_by (Optional[Union[str, List]], optional): 
            Identifiers to be balanced. Defaults to None.
        weighted_streams (int):
            Calculates sampling weights for all streams and applies them during
            sampling. To be combined with nb_samples not `None`.
            Defaults to `False`.
        cache_requests (bool, optional): Enable GBIF API cache.
            Can significantly improve API requests. Defaults to False.
        mediatype (str):
            supported GBIF media type. Can be `StillImage`, `MovingImage`, `Sound`.
            Defaults to `StillImage`.

    Returns:
        Iterable: generate-like object, that yields dictionaries
    """
    streams = []
    # set pygbif api caching
    pygbif.caching(cache_requests)

    # copy queries since we delete keys from the dict
    q = queries.copy()

    # if weighted_streams and nb_samples_per_stream is not None:
    #     raise RuntimeError("weights can only be applied when the number of samples are limited.")

    # Split queries into product of streamers
    if split_streams_by is not None:
        balance_queries = {}
        # if single string is provided, covert into list
        if isinstance(split_streams_by, str):
            split_streams_by = [split_streams_by]

        # remove balance_by from query and move to balance_queries
        for key in split_streams_by:
            balance_queries[key] = q.pop(key)

        # for each b in balance_queries, create a separate stream
        # later we control the sampling processs of these streams to balance
        for b in dproduct(balance_queries):
            # for each stream we wrap into pescador Streamers for additional features
            streams.append(
                pescador.Streamer(
                    pescador.Streamer(
                        gbif_query_generator,
                        label=label,
                        mediatype=mediatype,
                        **q,
                        **b
                    ),
                    # this makes sure that we only obtain a maximum number
                    # of samples per stream
                    max_iter=nb_samples_per_stream
                )
            )
        # count the available occurances for each stream and select the minimum.
        # We only yield the minimum of streams to balance
        if nb_samples == -1:
            # calculate the miniumum number of samples available per stream
            nb_samples = min(
                [
                    gbif_count(mediatype=mediatype, **q, **b) for b in dproduct(balance_queries)
                ]
            ) * len(streams)

        if weighted_streams:
            weights = np.array([float(gbif_count(mediatype=mediatype, **q, **b)) for b in dproduct(balance_queries)])
            weights /= np.max(weights)
        else:
            weights = None

        mux = pescador.StochasticMux(
            streams,
            n_active=len(streams),  # all streams are always active.
            rate=None,  # all streams are balanced
            weights=None,  # weight streams
            mode="exhaustive"  # if one stream fails it is not revived
        )

        return mux(max_iter=nb_samples)

    # else there will be only one stream, hence no balancing or sampling
    else:
        nb_samples = min(nb_samples_per_stream, nb_samples)
        return pescador.Streamer(gbif_query_generator, label=label, **q, )


