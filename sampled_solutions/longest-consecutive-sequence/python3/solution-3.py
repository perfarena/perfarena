# Create a set from the array
num_set = set(nums)
# Initialize a variable to track the longest sequence length
length = 0

# Traverse the array to find consecutive sequences
for start in nums:
    if start - 1 not in num_set:
        end = start + 1
        while end in num_set:
            end += 1
        length = max(length, end - start)

# Return the longest consecutive sequence length
return length