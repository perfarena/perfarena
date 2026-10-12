function firstMissingPositive(nums) {
    const unique = {};
    const positiveUnique = nums.filter(num => {
        if (num > 0 && !unique[num]) {
            unique[num] = true;
            return true;
        }
        return false;
    });

    positiveUnique.sort((a, b) => a - b);

    let expected = 1;
    for (const num of positiveUnique) {
        if (num === expected) {
            expected++;
        } else {
            return expected;
        }
    }

    return expected;
}

// Test cases
console.log(firstMissingPositive([1, 2, 0])); // Output: 3
console.log(firstMissingPositive([3, 4, -1, 1])); // Output: 2
console.log(firstMissingPositive([7, 8, 9, 11, 12])); // Output: 1