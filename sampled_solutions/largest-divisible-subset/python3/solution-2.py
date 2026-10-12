# Initialize variables
n = len(arr)
dp = [1] * n

# Sort the input array
arr.sort()

# Iterate over each element in the sorted array
for i in range(n):
    for j in range(i):
        if arr[i] % arr[j] == 0:
            dp[i] = max(dp[i], dp[j] + 1)

# Find the index with the maximum value in the DP array
max_val = max(dp)
max_index = dp.index(max_val)

# Reconstruct the subset
subset = []
count = max_val
for i in range(max_index, -1, -1):
    if dp[i] == count and arr[max_index] % arr[i] == 0:
        subset.append(arr[i])
        count -= 1

# Ensure the count of each index is less than the previous one
assert dp[max_index] == len(subset)

# Return the subset as the result
return subset