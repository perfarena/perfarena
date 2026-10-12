def max_partition_lengths(s):
    last_occurrence = {}
    for i, char in enumerate(s):
        last_occurrence[char] = i

    n = len(s)
    left, right = 0, 0
    max_partition = 0
    answer = []

    while right < n:
        max_partition = max(max_partition, last_occurrence[s[right]])
        if max_partition == right:
            answer.append(right - left + 1)
            left = right + 1
        right += 1

    return answer

# Test the function
s = "aabbcc"
print(max_partition_lengths(s))  # Output: [2, 2, 2]