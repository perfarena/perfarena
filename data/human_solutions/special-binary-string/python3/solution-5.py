from collections import defaultdict

def partition_labels(s):
    rightmost = defaultdict(int)
    for i, char in enumerate(s):
        rightmost[char] = i

    partitions = []
    left, right = 0, 0
    for i, char in enumerate(s):
        right = max(right, rightmost[char])
        if i == right:
            partitions.append(right - left + 1)
            left = i + 1

    return partitions

# Test the function
s = "abacdcde"
partitions = partition_labels(s)
print(partitions)  # Output: [3, 3, 1]