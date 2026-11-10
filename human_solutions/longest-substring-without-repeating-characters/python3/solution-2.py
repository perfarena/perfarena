def longest_substring(s):
    result = 0
    left = 0
    char_set = set()

    for right in range(len(s)):
        while s[right] in char_set:
            char_set.remove(s[left])
            left += 1

        char_set.add(s[right])
        result = max(result, right - left + 1)

    return result