from collections import Counter

def minimum_deletion(s):
    count = Counter(s)
    deletion = 0
    used_frequencies = set()
    
    for frequency in count.items():
        while frequency[1] > 0 and frequency[0] in used_frequencies:
            frequency = (frequency[0], frequency[1] - 1)
            deletion += 1
        used_frequencies.add(frequency[0])
    
    return deletion

# Example usage
s1 = "aab"
s2 = "aabbcc"

print(minimum_deletion(s1))  # Output: 0
print(minimum_deletion(s2))  # Output: 2