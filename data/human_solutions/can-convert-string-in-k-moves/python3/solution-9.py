def minDeletions(s: str) -> int:
    freq = [0] * 26
    hash_set = set()
    deletion_count = 0

    for char in s:
        freq[ord(char) - ord('a')] += 1

    for f in freq:
        while f > 0 and f in hash_set:
            f -= 1
            deletion_count += 1
        if f > 0:
            hash_set.add(f)

    return deletion_count