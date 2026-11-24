// Initialize an array to store character counts
vector<int> alpha(26, 0);

// Iterate through the string and count characters
for (int i = 0; i < s.size(); i++) {
    alpha[s[i] - 'a']++;
}

// Set the standard count
int standard = -1;
for (int i = 0; i < alpha.size(); i++) {
    if (standard == -1 && alpha[i] != 0) {
        standard = alpha[i];
    } else if (standard != -1 && alpha[i] != 0) {
        if (alpha[i] != standard) {
            return false;
        }
    }
}

return true;