from django import forms
from django.utils import timezone

from .models import Person, Project


class ManualMeetingForm(forms.Form):
    title = forms.CharField(label="会议主题", max_length=200,
                            widget=forms.TextInput(attrs={"placeholder": "例如：订单履约项目周会", "autofocus": True}))
    meeting_date = forms.DateField(label="会议日期", initial=timezone.localdate,
                                   widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    meeting_time = forms.TimeField(label="开始时间", required=False,
                                   widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    agenda = forms.CharField(label="本次议题", required=False, max_length=10000,
                             widget=forms.Textarea(attrs={"rows": 3, "placeholder": "准备讨论什么？也可以开始后再补充。"}))
    projects = forms.ModelMultipleChoiceField(label="关联项目", required=False, queryset=Project.objects.all(),
                                              widget=forms.CheckboxSelectMultiple)
    people = forms.ModelMultipleChoiceField(label="汇报人员", required=False, queryset=Person.objects.filter(is_active=True),
                                            widget=forms.CheckboxSelectMultiple)

    def meeting_state(self):
        values = self.cleaned_data
        return {
            "title": values["title"], "meeting_date": values["meeting_date"].isoformat(),
            "meeting_time": values["meeting_time"].strftime("%H:%M") if values["meeting_time"] else "",
            "agenda": values["agenda"], "project_ids": [p.pk for p in values["projects"]],
            "person_ids": [p.pk for p in values["people"]], "items": [],
        }
