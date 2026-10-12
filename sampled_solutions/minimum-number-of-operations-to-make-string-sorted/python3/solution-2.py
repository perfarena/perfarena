from collections import Counter

# Create a dictionary representation of the string
d = Counter(s)
print(d)

# Check if all values in the dictionary are the same
maximum = max(d.values())
minimum = min(d.values())
return maximum == minimum