# Initialize a set to store unique values
hash_set = set()

# Loop through the first array to add unique values to the set
for num in numbers1:
    if num not in hash_set:
        hash_set.add(num)

# Initialize an output set to store the intersection
outset = set()

# Loop through the second array to find the intersection
for num in numbers2:
    if num in hash_set:
        outset.add(num)

# Return the output set containing the intersection of the two arrays
return outset