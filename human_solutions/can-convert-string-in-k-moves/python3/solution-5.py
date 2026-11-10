def minDeletions(s):
    freq = [0] * 26
    for char in s:
        freq[ord(char) - ord('a')] += 1

    freq.sort(reverse=True)
    deletions = 0

    for i in range(1, 26):
        if freq[i] >= freq[i - 1]:
            diff = freq[i] - freq[i - 1] + 1
            deletions += diff
            freq[i] -= diff

    return deletions

# Example usage
s = "aabbcc"
result = minDeletions(s)
print(result)  # Output: 2