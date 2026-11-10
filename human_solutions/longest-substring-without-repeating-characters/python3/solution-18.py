def length_of_longest_substring(s):
    d = set()
    left = 0
    count = 0

    for right in range(len(s)):
        while s[right] in d:
            d.remove(s[left])
            left += 1

        d.add(s[right])
        count = max(count, right - left + 1)

    return count