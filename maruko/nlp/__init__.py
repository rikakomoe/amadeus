import json
import random
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Union

import jieba_fast
import requests
from none import get_bot

from maruko import aio
from . import baidu_aip, ltp_cloud


async def sentence_similarity(sentence1: str, sentence2: str) -> float:
    """Basic sentence similarity calculation."""
    if sentence1 == sentence2:
        return 1.00
    if not sentence1 and sentence2 or not sentence2 and sentence1:
        return 0.00
    return await baidu_aip.simnet(sentence1, sentence2)


class ExampleSentence:
    """
    Represent an example sentence when calculating similarity.
    """
    __slots__ = ('text', 'avg_score', 'total_compare', 'solid')

    def __init__(self, text: str, *,
                 _avg_score: float = 0.00,
                 _total_compare: int = 0,
                 _solid: bool = True):
        self.text = text
        self.avg_score = _avg_score
        self.total_compare = _total_compare
        self.solid = _solid

    def __repr__(self):
        return f'<ExampleSentence (text={self.text}, ' \
               f'avg_score={self.avg_score}, ' \
               f'total_compare={self.total_compare}, ' \
               f'solid={self.solid})>'


async def sentence_similarity_ex(
        sentence: str,
        example_sentences: List[ExampleSentence],
        max_example_sentences: int = 6,
        keep_solid_sentence: bool = True,
        ok_score: float = 0.70,
        great_score: float = 0.80) -> Tuple[float, bool]:
    """
    Sentence similarity calculation with extra functions.

    :param sentence: sentence to check
    :param example_sentences: example sentences to compare with
    :param max_example_sentences: max number of example sentences
    :param keep_solid_sentence: keep solid eg sentences when reaching max num
    :param ok_score: score that we think the similarity is ok
    :param great_score: score that we think the similarity is great
    :return: tuple of final score and match or not
    """
    max_score = 0.00
    for es in example_sentences:
        curr_score = await sentence_similarity(sentence, es.text)
        max_score = max(max_score, curr_score)
        if curr_score >= ok_score:
            # this is an ok match, update the average score
            curr_total_score = \
                es.avg_score * es.total_compare + curr_score
            es.total_compare += 1
            es.avg_score = curr_total_score / es.total_compare

            if curr_score >= great_score:
                # this is a great match,
                # we record the text, and make it an example sentence
                new_es = ExampleSentence(sentence,
                                         _avg_score=curr_score,
                                         _total_compare=1,
                                         _solid=False)
                example_sentences.append(new_es)

            # sort the example sentences to make sure
            # the most possible sentence will be checked first
            example_sentences.sort(key=lambda x: x.avg_score, reverse=True)

            # remove the example sentences with too little average score,
            # however keeping the solid ones if needed
            diff_len = len(example_sentences) - max_example_sentences
            if diff_len > 0:
                for i in range(len(example_sentences) - 1, -1, -1):
                    if not (keep_solid_sentence and
                            example_sentences[i].solid):
                        del example_sentences[i]
                    diff_len -= 1
                    if diff_len == 0:
                        break

            # since we are ok, break the for loop
            break

    return max_score, max_score >= ok_score


# List of paragraphs (split by lines), and each paragraph is a list of words
LexerResult_T = List[List[Dict[str, Any]]]


async def lexer(text: str) -> LexerResult_T:
    """
    A lexer that segment the input text and do POS tagging and NER on it.

    :param text: the input text (may have multiple paragraphs)
    :return: the lexical analysis result
    """
    text = text.strip()
    if not text:
        return []

    lexer_vendors = [
        (_lexer_baidu_aip, 0.4),
        (_lexer_ltp_cloud, 0.6),
    ]

    f = random.choices(*zip(*lexer_vendors))[0]
    return await f(text)


async def _lexer_baidu_aip(text: str) -> LexerResult_T:
    assert text

    baidu_aip_result = await baidu_aip.lexer(text)
    result = []

    paragraph = []
    temp_data = {}

    def collect_named_entity_from_temp():
        paragraph.append({
            'item': ''.join(temp_data['basic_words']),
            'basic_words': temp_data['basic_words'],
            'ne': temp_data['ne_type'],
            'pos': ''
        })

    for word in baidu_aip_result:
        if temp_data.get('in_loc_ne') and word['ne'] != 'LOC':
            # a LOC named entity ends
            collect_named_entity_from_temp()
            temp_data.clear()

        if '\n' in word['item'] and paragraph:
            result.append(paragraph)
            paragraph = []
            continue

        # merge LOC named entity
        if not temp_data.get('in_loc_ne') and word['ne'] == 'LOC':
            # a LOC named entity begins
            temp_data['in_loc_ne'] = True
            temp_data['basic_words'] = word['basic_words']
            temp_data['ne_type'] = 'LOC'
            continue
        elif temp_data.get('in_loc_ne') and word['ne'] == 'LOC':
            # in a LOC named entity
            temp_data['basic_words'].extend(word['basic_words'])
            continue

        # a normal word
        paragraph.append({
            'item': word['item'],
            'basic_words': word['basic_words'],
            'ne': word['ne'],
            'pos': word['pos']
        })

    if temp_data.get('in_loc_ne'):
        collect_named_entity_from_temp()

    if paragraph:
        result.append(paragraph)

    return result


async def _lexer_ltp_cloud(text: str) -> LexerResult_T:
    assert text

    ltp_cloud_result = await ltp_cloud.lexer(text)
    result = []

    ne_type_map = {
        'nh': 'PER',
        'ni': 'ORG',
        'ns': 'LOC',
    }

    pos_map = {
        'b': 'a',
        'e': 'y',
        'g': 'x',
        'nd': 'f',
        'nh': 'nr',
        'ni': 'nt',
        'nl': 's',
        'nt': 't',
        'wp': 'w',
        'ws': 'xf',
    }

    for paragraph in ltp_cloud_result:
        paragraph = sum(paragraph, [])

        paragraph_normalized = []
        temp_data = {}

        def collect_named_entity_from_temp():
            paragraph_normalized.append({
                'item': ''.join(temp_data['basic_words']),
                'basic_words': temp_data['basic_words'],
                'ne': temp_data['ne_type'],
                'pos': ''
            })

        for word in paragraph:
            if temp_data.get('in_ne') and temp_data['ne_type'] == 'TIME' and \
                    word['pos'] != 'nt':
                # a TIME named entity ends
                collect_named_entity_from_temp()
                temp_data.clear()

            if '-' in word['ne']:
                # LTP Cloud thought this word is a named entity
                ne_mark, ne_type = word['ne'].split('-')
                ne_type = ne_type_map.get(ne_type.lower())
                if ne_type:
                    if ne_mark == 'S':
                        # a single word named entity
                        paragraph_normalized.append({
                            'item': word['cont'],
                            'basic_words': [word['cont']],
                            'ne': ne_type,
                            'pos': pos_map.get(word['pos'], word['pos'])
                        })
                        temp_data.clear()
                    elif ne_mark == 'B':
                        # a multi-words named entity begins
                        temp_data['in_ne'] = True
                        temp_data['basic_words'] = [word['cont']]
                        temp_data['ne_type'] = ne_type
                        temp_data['last_pos'] = word['pos']
                    elif ne_mark == 'I':
                        temp_data['basic_words'].append(word['cont'])
                        temp_data['last_pos'] = word['pos']
                    elif ne_mark == 'E':
                        # a multi-words named entity ends
                        temp_data['basic_words'].append(word['cont'])
                        collect_named_entity_from_temp()
                        temp_data.clear()

                    # we've handle this word as a named entity,
                    # continue to next word here
                    continue

            # recognize TIME named entity
            if not temp_data.get('in_ne') and word['pos'] == 'nt':
                # a TIME named entity begins
                temp_data['in_ne'] = True
                temp_data['basic_words'] = [word['cont']]
                temp_data['ne_type'] = 'TIME'
                temp_data['last_pos'] = word['pos']
                continue
            elif temp_data.get('in_ne') and \
                    temp_data['ne_type'] == 'TIME' and \
                    word['pos'] == 'nt':
                # in a TIME named entity
                temp_data['basic_words'].append(word['cont'])
                temp_data['last_pos'] = word['pos']
                continue

            # a normal word
            paragraph_normalized.append({
                'item': word['cont'],
                'basic_words': [word['cont']],
                'ne': '',
                'pos': pos_map.get(word['pos'], word['pos'])
            })
            temp_data.clear()

        if temp_data.get('in_ne'):
            if temp_data['ne_type'] == 'ORG' and \
                    temp_data.get('last_pos') == 'ns':
                # this may be a bug of LTP Cloud
                # '南京' will be recognized as B-Ni (beginning of ORG),
                # we fix it here, but may introduce other bugs
                temp_data['ne_type'] = 'LOC'
            collect_named_entity_from_temp()

        result.append(paragraph_normalized)

    return result


@dataclass
class Location:
    province: str = None
    city: str = None
    district: str = None
    other: str = None


async def parse_location(
        location_word: Union[str, List[str]]) -> Location:
    """
    Parse location like "江苏省南京市浦口区".

    :param location_word: location word (segmented or not)
    :return: Location object
    """
    if not location_word:
        return Location()

    if isinstance(location_word, str):
        location_words = jieba_fast.lcut(location_word)
    else:
        location_words = location_word

    location = Location()
    i = 0
    while i < len(location_words):
        if all((location.province, location.city, location.district)):
            # we are done with "省"、"市"、"区／县级市"
            break

        w = location_words[i].strip('省市区')
        if not w:
            i += 1
            continue

        try:
            # use HeWeather's API
            # TODO: 对 location 加缓存
            resp = await aio.run_sync_func(
                requests.get, 'https://search.heweather.com/find',
                params={
                    'location': w,
                    'key': get_bot().config.HEWEATHER_KEY,
                    'group': 'cn',
                })
            result = (await aio.run_sync_func(
                resp.json)).get('HeWeather6', [])[0]
        except (requests.RequestException, json.JSONDecodeError,
                AttributeError, IndexError):
            i += 1
            continue

        if result.get('status') != 'ok':
            i += 1
            continue

        # status is ok here, so there is at lease one location info
        basic = result.get('basic')[0]
        parsed = False
        if w == basic.get('admin_area'):
            location.province = w
            parsed = True
        if w == basic.get('parent_city'):
            # don't check parsed here, because we may encounter "北京",
            # of which city and province are the same
            location.city = w
            parsed = True
        if not parsed and w == basic.get('location'):
            location.district = w

        i += 1  # head on to the next

    location.other = ''.join(location_words[i:]) or None
    return location
