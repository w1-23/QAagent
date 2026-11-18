"""Conservative field observations; no truth verdict."""
import re
from . import providers as lit
FIELDS=("doi","title","authors","year","journal","volume","issue","pages","article_number")

def _value(claims, field):
    value = claims.get(field)
    if field == 'journal':
        value = value or claims.get('venue')
    if isinstance(value, (list, tuple)):
        return '; '.join(str(item) for item in value)
    return str(value or '').strip()


def _author_comparison(supplied, registered):
    truncated = bool(re.search(r'\bet\s+al\b', supplied, re.I))
    left = re.sub(r'\bet\s+al\.?', '', supplied, flags=re.I).strip(' .,;')
    right = re.sub(r'\bet\s+al\.?', '', registered, flags=re.I).strip(' .,;')
    if truncated:
        # et al describes an omitted suffix, not a disagreement in author count.
        right = re.split(r';|,|\s+and\s+', right)[0]
    if lit._author_conflict(left, right):
        return 'conflicting', 'different_authors'

    def abbreviated_name(value):
        tokens = re.findall(r'[^\W\d_]+', value, re.UNICODE)
        if len(tokens) < 2:
            return None
        for surname, given in ((tokens[0], tokens[1:]), (tokens[-1], tokens[:-1])):
            if len(surname) > 1 and all(len(t) == 1 or (t.isupper() and len(t) <= 4) for t in given):
                return lit.norm_title(surname), ''.join(given).casefold()
        return None

    # Compare explicit initials for the first author without demanding equal
    # author count, full given names or transliteration.
    first_left, first_right = (re.split(r';|,|\s+and\s+', value)[0] for value in (left, right))
    abbreviated = abbreviated_name(first_left)
    if abbreviated:
        surname, initials = abbreviated
        other = abbreviated_name(first_right)
        if other and surname == other[0] and initials[0] != other[1][0]:
            return 'conflicting', 'different_author_initials'
        tokens = lit.norm_title(first_right).split()
        if not other and len(tokens) >= 2:
            if tokens[-1] == surname and tokens[0][0] != initials[0]:
                return 'conflicting', 'different_author_initials'
    return 'uncertain', 'abbreviated_or_incomplete_authors' if truncated else 'author_order_count_or_transliteration_requires_review'


def _journal_comparison(supplied, registered):
    left, right = (lit.norm_title(value).split() for value in (supplied, registered))
    skip = {'of', 'the', 'and', 'in', 'for', 'de', 'a'}
    a, b = ([word for word in words if word not in skip] for words in (left, right))
    # Abbreviated titles and acronyms are plausible variants, not certified aliases.
    acronym = lambda words: ''.join(word[0] for word in words)
    compatible = (a and b and (''.join(a) == acronym(b) or ''.join(b) == acronym(a)
                  or (len(a) == len(b) and all(x.startswith(y) or y.startswith(x) for x, y in zip(a, b)))
                  or set(a).issubset(b) or set(b).issubset(a)))
    if compatible:
        return 'uncertain', 'possible_journal_abbreviation_or_related_venue'
    # Very short strings may be opaque acronyms outside the known words.
    if any(len(lit.norm_title(value).replace(' ', '')) <= 4 and (value.isupper() or '.' in value)
           for value in (supplied, registered)):
        return 'uncertain', 'unresolved_journal_abbreviation'
    return 'conflicting', 'different_journal'


def compare_fields(claims, candidate, *, uncertain_fields=()):
    """Conservative rules: variants are uncertain, never automatic tolerance passes."""
    checks = {}
    for field in FIELDS:
        supplied, registered = _value(claims, field), _value(candidate or {}, field)
        reason = ''
        if not supplied:
            state, reason = 'not_checked', 'not_submitted'
        elif candidate is None:
            state, reason = 'not_checked', 'no_identified_record'
        elif not registered:
            state, reason = 'missing', 'provider_field_missing'
        elif field == 'doi':
            state = 'matched' if lit.normalize_doi(supplied) == lit.normalize_doi(registered) else 'conflicting'
        elif field == 'year':
            years = {str(item.get('year')) for item in candidate.get('years', [])
                     if isinstance(item, dict) and item.get('kind') in {'published-online', 'published-print', 'issued', 'published'}}
            if supplied == registered or supplied in years:
                state, reason = 'matched', 'registered_publication_year'
            elif supplied.isdigit() and registered.isdigit() and abs(int(supplied) - int(registered)) == 1:
                state, reason = 'uncertain', 'possible_online_print_difference_not_documented'
            else:
                state = 'conflicting'
        else:
            left, right = lit.norm_title(supplied), lit.norm_title(registered)
            if field == 'authors' and re.search(r'\bet\s+al\b', supplied, re.I):
                state, reason = _author_comparison(supplied, registered)
            elif left and left == right:
                state = 'matched'
            elif field == 'title':
                state = 'uncertain' if lit.title_similarity(supplied, registered) >= .82 else 'conflicting'
                reason = 'title_variant' if state == 'uncertain' else 'different_title'
            elif field == 'authors':
                state, reason = _author_comparison(supplied, registered)
            elif field == 'journal':
                state, reason = _journal_comparison(supplied, registered)
            elif field == 'pages':
                state, reason = 'conflicting', 'page_range_differs_article_number_not_interchangeable'
            else:
                state = 'conflicting'
        comparison_state = state
        if field in uncertain_fields and supplied and state not in {'not_checked', 'missing'}:
            state, reason = 'uncertain', 'heuristic_input_field_requires_confirmation'
        checks[field] = {'status': state, 'submitted': supplied, 'registered': registered, 'reason': reason,
                         'input_certainty': 'uncertain' if field in uncertain_fields else 'submitted',
                         'comparison_status': comparison_state}
    return checks


def hint_differences(claims, record, *, uncertain_fields=()):
    return [{"field": field, "submitted": row["submitted"], "registered": row["registered"], "kind": row["status"], "reason": row["reason"], "input_certainty": row["input_certainty"]} for field,row in compare_fields(claims,record,uncertain_fields=uncertain_fields).items()]
