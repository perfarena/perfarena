// Initialize variables
let map = {};
let bucket = [];
let result = [];

// Create frequency map
for (let num of nums) {
    map[num] = (map[num] || 0) + 1;
}

// Populate the bucket
for (let [num, frequency] of Object.entries(map)) {
    if (!bucket[frequency]) {
        bucket[frequency] = new Set();
    }
    bucket[frequency].add(num);
}

// Loop through the bucket backwards
for (let i = bucket.length - 1; i >= 0; i--) {
    if (bucket[i]) {
        for (let num of bucket[i]) {
            result.push(num);
            if (result.length === k) {
                break;
            }
        }
    }
    if (result.length === k) {
        break;
    }
}

// Return the result array
return result;