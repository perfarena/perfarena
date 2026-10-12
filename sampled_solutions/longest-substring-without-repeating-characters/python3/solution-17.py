def longest_substring(s):
    if not s:
        return 0

    answer = 0
    i = 0
    j = 0
    map = {}

    while j < len(s):
        if s[j] not in map:
            map[s[j]] = j
            answer = max(answer, j - i + 1)
        else:
            i = max(i, map[s[j]] + 1)
            map[s[j]] = j

        j += 1

    return answer