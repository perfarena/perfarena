# Python code implementation of the algorithm
def min_window(s, t):
    target = {}
    count = {}
    missing = set(t)
    start = 0
    min_len = float('inf')
    min_start = 0

    for char in t:
        if char in target:
            target[char] += 1
        else:
            target[char] = 1

    for end, char in enumerate(s):
        if char in target:
            if char in count:
                count[char] += 1
            else:
                count[char] = 1

            if count[char] >= target[char]:
                missing.discard(char)

            while not missing:
                if end - start + 1 < min_len:
                    min_len = end - start + 1
                    min_start = start

                if s[start] in count:
                    count[s[start]] -= 1
                    if count[s[start]] < target[s[start]]:
                        missing.add(s[start])

                start += 1

    return "" if min_len == float('inf') else s[min_start:min_start + min_len]