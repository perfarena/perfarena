# Initialize variables
max_num = float('-inf')
min_num = float('inf')
hash_table = {}

# Iterate through the array to find max and min elements
for num in nums:
    hash_table[num] = hash_table.get(num, 0) + 1
    max_num = max(max_num, num)
    min_num = min(min_num, num)

# Set low and high values for pairing
low = min_num
high = max_num

# Pair up the elements to find the maximum pair sum
while low < high:
    if hash_table.get(low, 0) == 0:
        low += 1
    elif hash_table.get(high, 0) == 0:
        high -= 1
    else:
        max_num = max(max_num, low + high)
        hash_table[low] -= 1
        hash_table[high] -= 1

# Return the maximum pair sum
return max_num