def truncate_sentence_optimized(s, k):
    count = 0
    for i in range(len(s)):
        if s[i] == ' ':
            count += 1
            if count == k:
                return s[:i]
    return s