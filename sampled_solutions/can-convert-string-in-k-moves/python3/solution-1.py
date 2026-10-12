import collections

def minDeletions(s):
    deletions = 0
    character_counts = collections.Counter(s)
    frequency_set = set()

    for char, count in character_counts.items():
        while count > 0 and count in frequency_set:
            count -= 1
            deletions += 1
        frequency_set.add(count)

    return deletions

# Test the function
s = "aabbccc"
result = minDeletions(s)
print(result)  # Output should be 2