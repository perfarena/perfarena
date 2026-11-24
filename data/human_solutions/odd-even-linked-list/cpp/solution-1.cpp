// Check if the head is null or has only one node
if (!head || !head->next) {
    return head;
}

ListNode* odd = head;
ListNode* even = head->next;
ListNode* evenHead = even;

while (even && even->next) {
    odd->next = even->next;
    odd = odd->next;
    even->next = odd->next;
    even = even->next;
}

odd->next = evenHead;

return head;