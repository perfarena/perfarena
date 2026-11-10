struct ListNode {
    int val;
    ListNode *next;
    ListNode(int x) : val(x), next(nullptr) {}
};

ListNode* deleteDuplicates(ListNode* head) {
    ListNode* current = head;
    while (current && current->next) {
        if (current->val == current->next->val) {
            ListNode* toFree = current->next;
            current->next = current->next->next;
            free(toFree);
            continue;
        }
        current = current->next;
    }
    return head;
}