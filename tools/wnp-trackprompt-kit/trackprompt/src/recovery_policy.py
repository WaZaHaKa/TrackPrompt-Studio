"""Pure reconciliation decisions. This module never submits, kills or restarts work."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from wnp_host.model import require

@dataclass(frozen=True)
class Observation:
    raw: Literal['absent','partial','invalid','complete_unverified','verified']='absent'
    provider: Literal['unknown','unreachable','absent','queued','running','completed','failed']='unknown'
    submission: Literal['none','prepared','sending','accepted','rejected']='none'
    owner_held: bool=False
    identity_match: bool=True
    matches: int=0

@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    may_submit: bool=False

def decide(o:Observation)->Decision:
    require(o.raw in ('absent','partial','invalid','complete_unverified','verified'),'Invalid raw state.')
    require(o.provider in ('unknown','unreachable','absent','queued','running','completed','failed'),'Invalid provider observation.')
    require(o.submission in ('none','prepared','sending','accepted','rejected'),'Invalid submission state.')
    require(type(o.matches) is int and o.matches>=0,'Invalid provider match count.')
    if not o.identity_match:return Decision('hold_integrity','Artifact/workflow/input identity conflicts; preserve evidence.')
    if o.matches>1:return Decision('hold_duplicate','Multiple matching provider jobs; do not submit or cancel blindly.')
    if o.raw=='verified':return Decision('reuse_raw','Verified raw checkpoint is reusable; final video remains a separate stage.')
    if o.raw=='complete_unverified':return Decision('verify_raw','Validate existing complete frame set and provenance; no diffusion rerun.')
    if o.provider in ('queued','running'):return Decision('adopt','Observe the exact existing provider attempt; no duplicate submission.')
    if o.provider=='completed':return Decision('recover_outputs','Recover/download/verify existing provider outputs.')
    if o.provider=='failed':return Decision('failed_attempt','Record the actual provider error; apply separately authorized retry policy.')
    if o.raw in ('partial','invalid'):return Decision('hold_partial','Preserve partial/corrupt artifacts; no automatic overwrite.')
    if o.provider in ('unknown','unreachable'):return Decision('wait_provider','Provider state is unknown, not absent.')
    if o.submission in ('sending','accepted'):return Decision('hold_ambiguous','Queue/history absence cannot prove the earlier request was never accepted.')
    if not o.owner_held:return Decision('observe_only','No exclusive local run ownership; do not mutate execution.')
    if o.submission=='prepared':return Decision('submit_once','Durable attempt exists and begin_send has never been committed.',True)
    if o.submission=='none':return Decision('prepare_attempt','Create a durable correlated intent under the run lock before any provider call.')
    return Decision('failed_attempt','A rejected attempt must not silently become a new generation.')

def final_frames(seconds:int,fps:int)->int:
    require(type(seconds) is int and type(fps) is int and seconds>0 and fps>0,'Invalid final timeline specification.')
    return seconds*fps
