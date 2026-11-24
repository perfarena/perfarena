def minDeletions(s: str) -> int:
    freq = {}
    known_freqs = set()
    answer = 0

    # Fill dictionary with character frequencies
    for char in s:
        freq[char] = freq.get(char, 0) + 1

    # Iterate over dictionary
    for char, f in freq.items():
        while f > 0:
            if f not in known_freqs:
                known_freqs.add(f)
            else:
                answer += 1
                f -= 1

    return answer

# Test the function with a sample string
s = "aabbccc"
print(minDeletions(s))  # Output should be 4