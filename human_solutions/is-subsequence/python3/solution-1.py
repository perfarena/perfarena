def is_subsequence(s: str, t: str) -> bool:
    i, j = 0, 0
    while i < len(s) and j < len(t):
        if s[i] == t[j]:
            i += 1
        j += 1
    return i == len(s)

# Example usage
s = "ABC"
t = "AAGBCDS"
print(is_subsequence(s, t))  # Output: True