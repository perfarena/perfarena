let current = []; // Initialize current array
let previous = [1]; // Initialize previous array with the first element as 1

for (let i = 1; i <= rowIndex; i++) {
    current = [1]; // Each row starts with 1

    for (let j = 1; j < i; j++) {
        current.push(previous[j - 1] + previous[j]); // Calculate current row values
    }

    current.push(1); // Each row ends with 1
    previous = current; // Update previous row for the next iteration
}

return previous; // Return the row values at the specified index