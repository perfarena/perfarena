def count_unique_subsequences(s):
    first = {}
    last = {}
    for i, char in enumerate(s):
        if char not in first:
            first[char] = i
        last[char] = i

    count = 0
    for c in range(ord('a'), ord('z') + 1):
        c = chr(c)
        if c not in first:
            continue

        seen = set()
        for i in range(first[c], last[c] + 1):
            if s[i] != c:
                seen.add(s[i])

        count += len(seen)

    return count

# Test the function
s = "hey hey everybody this is Larry this is day 14 of the Leal day"
result = count_unique_subsequences(s)
print(result)