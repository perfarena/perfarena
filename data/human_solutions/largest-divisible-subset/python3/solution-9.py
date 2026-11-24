# Sort the input array
nums.sort()

# Initialize DP array to store subsets
DP = [list() for _ in range(len(nums))]

# Iterate over each element in the sorted array
for i in range(len(nums)):
    for j in range(i):
        if nums[i] % nums[j] == 0 and len(DP[j]) > len(DP[i]):
            DP[i] = DP[j].copy()
    DP[i].append(nums[i])

# Find the index of the largest subset
answer_length = 0
answer_index = -1
for i in range(len(nums)):
    if answer_length < len(DP[i]):
        answer_length = len(DP[i])
        answer_index = i

# Return the largest subset
return DP[answer_index]