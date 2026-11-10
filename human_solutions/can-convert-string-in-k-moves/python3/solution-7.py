from collections import Counter

def minDeletions(s):
    freq = Counter(s)
    res = 0
    used = set()

    for char, frequency in freq.items():
        while frequency > 0 and frequency in used:
            frequency -= 1
            res += 1
        used.add(frequency)

    return res

# Test the function
s = "aaabbbcc"
result = minDeletions(s)
print(result)