// Create set1 and add elements from array1
Set<Integer> set1 = new HashSet<>();
for (int num : array1) {
    set1.add(num);
}

// Traverse array2 and add elements to set2 if not already present in set1
Set<Integer> set2 = new HashSet<>();
for (int num : array2) {
    if (set1.contains(num)) {
        set2.add(num);
    }
}

// Convert set2 to resultant array
int[] result = new int[set2.size()];
int index = 0;
for (int num : set2) {
    result[index++] = num;
}

return result;