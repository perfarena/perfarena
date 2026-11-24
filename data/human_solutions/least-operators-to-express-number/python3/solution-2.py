def longest_ones_subarray(arr, K):
    max_length = 0
    zeros_count = 0
    j = 0

    for i in range(len(arr)):
        if arr[i] == 0:
            zeros_count += 1

        while zeros_count > K:
            if arr[j] == 0:
                zeros_count -= 1
            j += 1

        max_length = max(max_length, i - j + 1)

    return max_length

# Example usage
arr = [1, 1, 0, 0, 1, 1, 1, 0, 1]
K = 2
result = longest_ones_subarray(arr, K)
print(result)  # Output: 6