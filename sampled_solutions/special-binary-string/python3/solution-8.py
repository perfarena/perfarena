def max_partition_lengths(s):
    end_idx = [0] * 26
    result = []
    start = end = 0

    for i, char in enumerate(s):
        end_idx[ord(char) - ord('a')] = i

    for i, char in enumerate(s):
        last_idx = end_idx[ord(char) - ord('a')]
        if last_idx > end:
            end = last_idx
        if i == end:
            result.append(end - start + 1)
            start = end + 1

    return result